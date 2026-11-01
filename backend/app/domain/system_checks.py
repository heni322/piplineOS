"""Run all checks: is everything needed for prospecting working RIGHT NOW? One button on the Checks page.

Unlike the go-live readiness list (legal and sign-off items), these are live technical checks, each answered with
"ok", "warn", "fail" or "info", what was found, and what to do:

database and migrations · job queue (Valkey) · worker · AI (Gemini key and model, the local model, or the simulator) ·
Google Places · how emails are sent (dry-run or live) · each mailbox's connection · each sending domain's DNS ·
recent sending problems.

Read-only: nothing is sent, switched on or saved. The only outside calls are free ones: Gemini's model description
(no tokens used) and the local model's list. Google Places is NOT called here (every request is billed).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.domain.outbound import problems
from app.llm.factory import backend_name
from app.settings import Settings

Status = Literal["ok", "warn", "fail", "info"]
BACKEND_DIR = Path(__file__).resolve().parents[2]
GEMINI_URL = "https://generativelanguage.googleapis.com"


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    status: Status
    detail: str
    fix: str = ""


def _secret(value: Any) -> str:
    return value.get_secret_value() if value is not None and hasattr(value, "get_secret_value") else (value or "")


def expected_schema() -> str | None:
    """The newest migration in the code (what `alembic upgrade head` would apply)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def check_schema(conn: Connection) -> Check:
    current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    head = expected_schema()
    if current == head:
        return Check("schema", "Database and migrations", "ok", f"Database reachable, schema up to date ({current}).")
    return Check(
        "schema",
        "Database and migrations",
        "fail",
        f"The database is at migration {current or 'none'}, the code expects {head}.",
        "In backend/, run: uv run alembic upgrade head — then restart the API and the worker.",
    )


def check_valkey(settings: Settings) -> Check:
    try:
        import redis

        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
    except Exception:  # report, don't leak details
        return Check(
            "valkey",
            "Job queue (Valkey)",
            "fail",
            "Valkey cannot be reached: searches and sends cannot be queued.",
            "Start it (docker compose up -d valkey, or scripts/dev-setup.ps1) and check REDIS_URL.",
        )
    return Check("valkey", "Job queue (Valkey)", "ok", "Reachable.")


def _worker_ping() -> list[Any]:
    from app.workflows.celery_app import celery_app

    return list(celery_app.control.ping(timeout=1.5) or [])


def check_worker(ping: Callable[[], list[Any]] = _worker_ping) -> Check:
    try:
        replies = ping()
    except Exception:
        replies = []
    if replies:
        return Check("worker", "Background worker", "ok", f"{len(replies)} worker(s) answered.")
    return Check(
        "worker",
        "Background worker",
        "fail",
        "No worker answered: company searches, AI work and sends stay waiting.",
        "Start it in backend/: uv run celery -A app.workflows.celery_app worker --beat --loglevel=info",
    )


def check_ai(settings: Settings, transport: httpx.BaseTransport | None = None) -> Check:
    name = backend_name(settings, tenant_live=False, golive_complete=False)
    if name == "simulated":
        return Check(
            "ai",
            "AI",
            "info",
            "No real AI is used yet: the simulator writes plausible test outputs.",
            "To use Gemini while testing: LLM_ADAPTER_MODE=sandbox, LLM_SANDBOX_PROVIDER=gemini and GEMINI_API_KEY.",
        )
    if name == "local":
        try:
            with httpx.Client(transport=transport, timeout=3) as http:
                r = http.get(f"{settings.local_llm_url}/api/tags")
            models = [m.get("name", "") for m in r.json().get("models", [])] if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            models = None
        if models is None:
            return Check(
                "ai",
                "AI (local model)",
                "fail",
                f"The local model server at {settings.local_llm_url} does not answer.",
                "Start Ollama, or choose Gemini (LLM_SANDBOX_PROVIDER=gemini).",
            )
        if not any(m.split(":")[0] == settings.local_llm_model.split(":")[0] for m in models):
            return Check(
                "ai",
                "AI (local model)",
                "fail",
                f"Ollama answers but {settings.local_llm_model} is not installed.",
                f"Run: ollama pull {settings.local_llm_model}",
            )
        return Check("ai", "AI (local model)", "ok", f"{settings.local_llm_model} is ready on this machine.")
    if name == "anthropic":
        ok = bool(_secret(settings.anthropic_api_key))
        return Check(
            "ai",
            "AI (Claude)",
            "ok" if ok else "fail",
            "ANTHROPIC_API_KEY is set." if ok else "ANTHROPIC_API_KEY is missing.",
            "" if ok else "Set ANTHROPIC_API_KEY, or choose another provider.",
        )
    # Gemini: describe the model (free, no tokens): proves the key and the model name in one call
    key = _secret(settings.gemini_api_key)
    if not key:
        return Check(
            "ai", "AI (Gemini)", "fail", "Gemini is chosen but GEMINI_API_KEY is empty.", "Set GEMINI_API_KEY."
        )
    try:
        with httpx.Client(transport=transport, timeout=8) as http:
            r = http.get(f"{GEMINI_URL}/v1beta/models/{settings.gemini_model}", headers={"x-goog-api-key": key})
    except httpx.HTTPError:
        return Check(
            "ai",
            "AI (Gemini)",
            "fail",
            "Google's Gemini API cannot be reached from this server.",
            "Check the internet connection or proxy.",
        )
    if r.status_code == 200:
        return Check("ai", "AI (Gemini)", "ok", f"Key accepted, model {settings.gemini_model} available.")
    if r.status_code == 404:
        return Check(
            "ai",
            "AI (Gemini)",
            "fail",
            f"The key works but the model {settings.gemini_model} does not exist.",
            "Set GEMINI_MODEL to an available model, e.g. gemini-flash-latest.",
        )
    if r.status_code in (400, 401, 403):
        return Check(
            "ai",
            "AI (Gemini)",
            "fail",
            f"Google refused the key (HTTP {r.status_code}).",
            "Create or copy a valid key at https://aistudio.google.com/apikey and set GEMINI_API_KEY.",
        )
    return Check("ai", "AI (Gemini)", "warn", f"Google answered HTTP {r.status_code}; try again in a minute.")


def check_places(engine: Engine, settings: Settings) -> Check:
    from app.domain.paid.places import PROVIDER, usage

    if settings.free_only:
        return Check("places", "Google Places", "info", "Off: FREE_ONLY=true never calls a paid API.")
    mode = settings.places_adapter_mode
    if mode == "mock":
        return Check(
            "places",
            "Google Places",
            "info",
            "Simulated: searches are only recorded in the dry-run log.",
            "To search for real while testing: PLACES_ADAPTER_MODE=sandbox (each search is billed).",
        )
    if not _secret(settings.google_places_api_key):
        return Check(
            "places",
            "Google Places",
            "fail",
            f"Mode {mode} but GOOGLE_PLACES_API_KEY is empty.",
            "Set GOOGLE_PLACES_API_KEY.",
        )
    used = usage(engine, PROVIDER)
    cap = settings.places_monthly_cap
    status: Status = "fail" if used >= cap else "warn" if used >= cap * 0.8 else "ok"
    real = "real searches while testing" if mode == "sandbox" else "real searches for live clients"
    return Check(
        "places",
        "Google Places",
        status,
        f"Key set, {real}. {used} of {cap} searches used this month.",
        "Monthly cap reached: searching stops until next month, or raise PLACES_MONTHLY_CAP." if used >= cap else "",
    )


def check_sending_mode(conn: Connection, settings: Settings) -> Check:
    live_clients = int(conn.execute(text("SELECT count(*) FROM tenants WHERE live_mode")).scalar() or 0)
    if settings.global_go_live and settings.email_adapter_mode == "live" and live_clients:
        return Check("sending_mode", "How emails are sent", "ok", f"Live: real emails for {live_clients} client(s).")
    missing = [
        n
        for n, on in (
            ("GLOBAL_GO_LIVE=true", settings.global_go_live),
            ("EMAIL_ADAPTER_MODE=live", settings.email_adapter_mode == "live"),
            ("a client in live mode", live_clients > 0),
        )
        if not on
    ]
    return Check(
        "sending_mode",
        "How emails are sent",
        "info",
        "Dry-run: approved emails are recorded in the dry-run log, nothing reaches anyone. Missing for real "
        f"sending: {', '.join(missing)}.",
        "Follow docs/GO-LIVE-RUNBOOK.md (section C) when you are ready.",
    )


def check_mailboxes(conn: Connection) -> list[Check]:
    rows = (
        conn.execute(
            text("""SELECT i.address, i.status, i.oauth_status, t.legal_name FROM inboxes i
                JOIN tenants t ON t.id = i.tenant_id WHERE t.status <> 'offboarded' ORDER BY t.legal_name, i.address""")
        )
        .mappings()
        .all()
    )
    if not rows:
        return [
            Check(
                "mailboxes",
                "Mailboxes",
                "warn",
                "No sending mailbox is set up for any client.",
                "Add one on the Inboxes page.",
            )
        ]
    out = []
    for r in rows:
        label = f"Mailbox {r['address']} ({r['legal_name']})"
        if r["oauth_status"] != "ok":
            out.append(
                Check(
                    "mailbox",
                    label,
                    "fail",
                    f"Connection {r['oauth_status'].replace('_', ' ')}: it cannot send.",
                    "Reconnect it on the Inboxes page.",
                )
            )
        elif r["status"] == "paused":
            out.append(
                Check(
                    "mailbox",
                    label,
                    "warn",
                    "Paused: nothing is sent from it.",
                    "Resume it on the Inboxes page once the cause is fixed.",
                )
            )
        else:
            out.append(Check("mailbox", label, "ok", f"Connected ({r['status']})."))
    return out


def check_domains(conn: Connection) -> list[Check]:
    rows = (
        conn.execute(
            text("""SELECT d.domain, d.spf_ok, d.dkim_ok, d.dmarc_ok, d.status, t.legal_name FROM sending_domains d
                JOIN tenants t ON t.id = d.tenant_id WHERE t.status <> 'offboarded' ORDER BY d.domain""")
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        label = f"Sending domain {r['domain']} ({r['legal_name']})"
        bad = [n for n, ok in (("SPF", r["spf_ok"]), ("DKIM", r["dkim_ok"]), ("DMARC", r["dmarc_ok"])) if not ok]
        if bad:
            out.append(
                Check(
                    "domain",
                    label,
                    "fail",
                    f"Not verified: {', '.join(bad)} missing or wrong.",
                    "Fix the DNS records, then click Check DNS on the Inboxes page.",
                )
            )
        elif r["status"] == "paused":
            out.append(Check("domain", label, "warn", "SPF, DKIM and DMARC are fine, but the domain is paused."))
        else:
            out.append(Check("domain", label, "ok", f"SPF, DKIM and DMARC verified ({r['status']})."))
    return out


def check_sending_problems(conn: Connection) -> Check:
    errors = problems.error_count(conn, days=7)
    if errors:
        return Check(
            "sending_problems",
            "Emails that failed",
            "fail",
            f"{errors} email(s) failed or are stuck in the last 7 days.",
            "See Sending problems below for each reason and what to do.",
        )
    return Check("sending_problems", "Emails that failed", "ok", "No failed email in the last 7 days.")


def run_all(
    engine: Engine,
    conn: Connection,
    settings: Settings,
    *,
    worker_ping: Callable[[], list[Any]] = _worker_ping,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, str]]:
    """Every check, in the order a problem should be fixed. `conn` is an operator connection (all clients)."""
    checks: list[Check] = [
        check_schema(conn),
        check_valkey(settings),
        check_worker(worker_ping),
        check_ai(settings, transport),
        check_places(engine, settings),
        check_sending_mode(conn, settings),
        *check_mailboxes(conn),
        *check_domains(conn),
        check_sending_problems(conn),
    ]
    return [asdict(c) for c in checks]
