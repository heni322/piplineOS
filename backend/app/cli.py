"""pipelineos CLI: keys, config sync, user creation, demo traces."""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
from pathlib import Path

import pyotp
import typer
from sqlalchemy import text

from app.config_sync import sync_all
from app.core.crypto import PiiCipher, blind_index
from app.core.logging import configure_logging
from app.core.security import hash_password, new_totp_secret
from app.db.session import DbContext, get_engine, transaction
from app.settings import get_settings

app = typer.Typer(no_args_is_help=True, help="PipelineOS administration")
keys_app = typer.Typer(help="Cryptographic keys")
config_app = typer.Typer(help="Versioned configuration")
users_app = typer.Typer(help="Users")
demo_app = typer.Typer(help="Synthetic demo data (dev/test only)")
registers_app = typer.Typer(help="Public company registers")
signals_app = typer.Typer(help="Free signal and contact sources: one live check each (read-only)")
app.add_typer(keys_app, name="keys")
app.add_typer(config_app, name="config")
app.add_typer(users_app, name="users")
app.add_typer(demo_app, name="demo")
app.add_typer(registers_app, name="registers")
app.add_typer(signals_app, name="signals")


@app.callback()
def _setup() -> None:
    """Logs go to stderr so command output (JSON) stays machine-readable."""
    configure_logging(os.environ.get("LOG_LEVEL", "WARNING"), stream=sys.stderr)


DEFAULT_CONFIG = Path(os.environ.get("PIPELINEOS_CONFIG", Path(__file__).resolve().parents[2] / "config"))


@keys_app.command("generate")
def keys_generate() -> None:
    """Print fresh 32-byte keys for .env (never commit them)."""
    for name in ("PII_MASTER_KEY", "BLIND_INDEX_KEY", "SUPPRESSION_HASH_KEY", "UNSUBSCRIBE_SIGNING_KEY"):
        typer.echo(f"{name}={base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}")


@config_app.command("sync")
def config_sync(path: Path = DEFAULT_CONFIG) -> None:
    with transaction(DbContext.system(), get_engine()) as conn:
        report = sync_all(conn, path)
    typer.echo(json.dumps({"created": report.created, "unchanged": len(report.unchanged)}, indent=2))


@registers_app.command("import")
def import_register(country: str, source: Path) -> None:
    """Import a public register's open-data file into the local copy: `be` (KBO/BCE full extract, free after
    registration with FPS Economy) or `ie` (CRO "Company Records", CC BY 4.0). Legal persons only; never deletes.
    Runs with the database owner account."""
    from app.adapters.registries.belgium import import_kbo
    from app.adapters.registries.ireland import import_cro

    importers = {"be": import_kbo, "ie": import_cro}
    if country.lower() not in importers:
        raise typer.BadParameter("country must be 'be' or 'ie'")
    r = importers[country.lower()](get_engine(get_settings().database_owner_url), source)
    typer.echo(json.dumps(r.__dict__, indent=2, default=str))


@signals_app.command("check")
def check_sources(
    source: str = typer.Argument(..., help="ted | job-board | gazette | website | officers | stream"),
    target: str = typer.Option("", help="ted: country (FR); job-board: greenhouse:name; others: company number"),
    name: str = typer.Option("", help="website: the company name"),
    query: str = typer.Option("IT; software; service", help="keywords for ted / job-board"),
    days: int = typer.Option(14, help="look back this many days"),
) -> None:
    """Call one free source once, live, and print what came back: counts and company-level samples only (no
    personal data is printed and nothing is stored). Use it to confirm a source's real format after setup."""
    from datetime import UTC, datetime, timedelta

    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=days)
    out: dict[str, object] = {"source": source}
    if source == "ted":
        from app.adapters.signals.ted import TedAwardSource

        sigs = TedAwardSource([target or "FR"], max_pages=1).fetch(query, since)
        out |= {"signals": len(sigs), "sample": [(s.company_name, s.country_hint, s.title) for s in sigs[:5]]}
    elif source == "job-board":
        from app.adapters.signals.job_boards import JobBoardSource, WatchedBoard

        jb = JobBoardSource([WatchedBoard("checked company", target)])
        sigs = jb.fetch("" if query == "IT; software; service" else query, since)
        out |= {"failed": jb.failed, "signals": [s.text for s in sigs]}
    elif source == "gazette":
        from app.adapters.insolvency import GazetteNotices

        out |= {"insolvency_notices": GazetteNotices().recent(target)}
    elif source == "website":
        from app.adapters.website_contacts import WebsiteFinder

        site = WebsiteFinder().find(target, name or None, None)
        out |= {"site": site.__dict__ if site else None}
    elif source == "officers":
        from app.adapters.companies_house.officers import CompaniesHouseOfficers

        key = settings.companies_house_api_key
        if key is None:
            raise typer.BadParameter("COMPANIES_HOUSE_API_KEY is not set")
        people = CompaniesHouseOfficers(key.get_secret_value()).find_people("", [], company_number=target)
        out |= {"current_officers": len(people), "titles": [p.title for p in people]}  # titles only, no names
    elif source == "stream":
        from app.adapters.companies_house.stream import FilingStream

        skey = settings.companies_house_stream_key
        if skey is None:
            raise typer.BadParameter("COMPANIES_HOUSE_STREAM_KEY is not set")
        sl = FilingStream(skey.get_secret_value()).read(None, max_events=500, max_seconds=60)
        out |= {
            "events_read": sl.read,
            "last_timepoint": sl.last_timepoint,
            "triggers": [(e.company_number, e.filing_type, str(e.filed_on)) for e in sl.events[:10]],
        }
    else:
        raise typer.BadParameter("source must be ted, job-board, gazette, website, officers or stream")
    typer.echo(json.dumps(out, indent=2, default=str))


@users_app.command("create-operator")
def create_operator(email: str, password: str = typer.Option(..., prompt=True, hide_input=True)) -> None:
    """Create an operator with mandatory TOTP MFA. Prints the otpauth URI once — add it to an authenticator app."""
    s = get_settings()
    cipher = PiiCipher(s.key("pii_master_key"))
    secret = new_totp_secret()
    with transaction(DbContext(role="operator"), get_engine()) as conn:
        conn.execute(
            text("""INSERT INTO users (email_bidx, email_enc, password_hash, role, mfa_secret_enc, mfa_enabled)
              VALUES (:b, :e, :p, 'operator', :m, true)"""),
            {
                "b": blind_index(s.key("blind_index_key"), email, None),
                "e": cipher.encrypt(conn, None, "users.email", email),
                "p": hash_password(password),
                "m": cipher.encrypt(conn, None, "users.mfa_secret", secret),
            },
        )
    typer.echo("Operator created. Scan this into your authenticator app (shown once):")
    typer.echo(pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="PipelineOS"))


@demo_app.command("traces")
def demo_traces(path: Path = DEFAULT_CONFIG) -> None:
    """Run the 5 synthetic dry-run compliance traces against the configured (dev) database."""
    s = get_settings()
    if s.environment in ("staging", "prod"):
        raise typer.BadParameter("demo traces are for dev/test databases only")
    from app.demo.traces import run_traces

    typer.echo(json.dumps(run_traces(get_engine(), s, path), indent=2, default=str))


@demo_app.command("seed")
def demo_seed() -> None:
    """Create the fictional demo tenant, config, campaign and inbox; enable GB in THIS (dev) database."""
    from app.demo.seed import seed_demo

    ids = seed_demo(get_engine(), get_settings())
    out = {"tenant_id": str(ids.tenant_id), "campaign_id": str(ids.campaign_id), "operator_email": ids.operator_email}
    if ids.operator_password:
        out |= {
            "operator_password": ids.operator_password,
            "operator_totp_uri": pyotp.TOTP(ids.operator_totp_secret or "").provisioning_uri(
                ids.operator_email, issuer_name="PipelineOS demo"
            ),
        }
        typer.echo("Demo operator created (credentials shown ONCE; dev database only).", err=True)
    if ids.client_password:
        out |= {"client_email": ids.client_email, "client_password": ids.client_password}
    typer.echo(json.dumps(out, indent=2))


@demo_app.command("run")
def demo_run() -> None:
    """Run Agent 1 -> 2 -> compliance -> 3 for the demo campaign (simulated LLM unless all live keys are on)."""
    from app.core.crypto import PiiCipher
    from app.demo.seed import providers, seed_demo
    from app.workflows.pipeline import agent_context, run_campaign

    s = get_settings()
    ids = seed_demo(get_engine(), s)
    ctx = agent_context(get_engine(), s, PiiCipher(s.key("pii_master_key")), ids.tenant_id, ids.campaign_id)
    run = run_campaign(ctx, providers())
    h = run.hunt
    typer.echo(
        json.dumps(
            {
                "signals_fetched": h.fetched,
                "deduplicated": h.deduplicated,
                "below_threshold": h.below_threshold,
                "discovered": h.discovered,
                "injection_flags": h.injection_flags,
                "lead_outcomes": run.outcomes,
            },
            indent=2,
        )
    )


@demo_app.command("send")
def demo_send() -> None:
    """Send approved demo messages that are due — DRY-RUN unless all three live keys are on."""
    from app.core.crypto import PiiCipher
    from app.demo.seed import seed_demo
    from app.workflows.pipeline import send_due

    s = get_settings()
    ids = seed_demo(get_engine(), s)
    res = send_due(get_engine(), s, PiiCipher(s.key("pii_master_key")), ids.tenant_id)
    typer.echo(
        json.dumps([{"status": r.status, "mode": r.mode, "reasons": list(r.reason_codes)} for r in res], indent=2)
    )


@demo_app.command("conversations")
def demo_conversations() -> None:
    """Phase 4 demo: approve, dry-run send, then simulated prospect replies handled by Agent 4 (booking, reminders,
    out-of-office, referral, unsubscribe, data-protection request). DEV DATABASE ONLY; time is simulated."""
    from app.core.crypto import PiiCipher
    from app.demo.conversations import run_conversations, summary
    from app.demo.seed import providers, seed_demo
    from app.workflows.pipeline import agent_context

    s = get_settings()
    if s.environment in ("staging", "prod"):
        raise typer.BadParameter("demo commands are disabled in staging/prod")
    ids = seed_demo(get_engine(), s)
    ctx = agent_context(get_engine(), s, PiiCipher(s.key("pii_master_key")), ids.tenant_id, ids.campaign_id)
    typer.echo(json.dumps(summary(run_conversations(ctx, providers(), ids.operator_id)), indent=2))


@demo_app.command("billing")
def demo_billing(period: str = "2026-10-01") -> None:
    """Phase 5 demo: draft the client's invoice for a month (after `demo conversations`). Drafts only: issuing is
    blocked until go-live, the accountant's sign-off and real supplier details."""
    from datetime import UTC, date, datetime

    from app.demo.seed import seed_demo
    from app.domain.billing import service as billing

    s = get_settings()
    if s.environment in ("staging", "prod"):
        raise typer.BadParameter("demo commands are disabled in staging/prod")
    ids = seed_demo(get_engine(), s)
    with transaction(DbContext(role="operator", user_id=ids.operator_id), get_engine()) as conn:
        res = billing.refresh_draft(
            conn,
            tenant_id=ids.tenant_id,
            period_start=date.fromisoformat(period),
            today=datetime.now(UTC).date(),
            actor_id=str(ids.operator_id),
        )
    typer.echo(
        json.dumps(
            {
                "invoice_id": str(res.invoice_id) if res.invoice_id else None,
                "lines": res.lines,
                "excluded": res.excluded,
                "total": billing.money(res.total_minor, "GBP"),
                "issue_blocked_by": res.blocked_reasons,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    app()
