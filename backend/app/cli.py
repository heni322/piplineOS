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
retention_app = typer.Typer(help="Data retention (config/retention.yaml; off until the solicitor sets the periods)")
app.add_typer(retention_app, name="retention")
economics_app = typer.Typer(help="Unit economics and funnel simulation (assumptions, not measurements)")
app.add_typer(economics_app, name="economics")
llm_app = typer.Typer(help="The free local AI model (Ollama): compare models on this server")
places_app = typer.Typer(help="Google Places (paid, gated, capped): find new target companies")
app.add_typer(places_app, name="places")
app.add_typer(llm_app, name="llm")


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

        site = WebsiteFinder(contact=settings.crawler_contact).find(target, name or None, None)
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


@economics_app.command("simulate")
def economics_simulate(
    price: float = typer.Option(300.0, help="EUR per qualified, attended meeting"),
    retainer: float = typer.Option(0.0, help="EUR monthly retainer"),
    hourly: float = typer.Option(30.0, help="EUR per operator hour"),
) -> None:
    """Pessimistic/base/optimistic funnel for 100 to 100k leads, a 1000-lead Monte Carlo and revenue targets."""
    from app.economics.simulator import report

    out = report(price_per_meeting_eur=price, monthly_retainer_eur=retainer, hourly_cost_eur=hourly)
    typer.echo(json.dumps(out, indent=2))


@retention_app.command("run")
def retention_run(apply: bool = typer.Option(False, help="Erase for real (default: only count what is due)")) -> None:
    """Count (or, with --apply, erase) prospects past the retention periods, per client."""
    from datetime import UTC, datetime

    from app.config_paths import config_root
    from app.domain.dpr.retention import RetentionPolicy, run_retention

    policy = RetentionPolicy.load(config_root())
    out: dict[str, object] = {"enabled": policy.enabled, "policy": policy.model_dump(), "clients": {}}
    if policy.enabled:
        engine = get_engine()
        with transaction(DbContext.system(), engine) as conn:
            tenants = conn.execute(text("SELECT id, slug FROM tenants ORDER BY slug")).all()
        now = datetime.now(UTC)
        for t in tenants:
            with transaction(DbContext.system(t.id), engine) as conn:
                r = run_retention(conn, t.id, policy, now, apply=apply)
            out["clients"][t.slug] = {"due": r.due, "erased": r.erased}  # type: ignore[index]
    typer.echo(json.dumps(out, indent=2))


@llm_app.command("benchmark")
def llm_benchmark(
    models: str = typer.Option(..., help="comma-separated Ollama tags, e.g. qwen3.6:35b,glm-4.7-flash,gpt-oss:20b"),
    sets: str = typer.Option("en,fr,nordic,stage4", help="reply sets: en, fr, nordic, stage4"),
    drafts: bool = typer.Option(True, help="also test email drafting (six synthetic leads)"),
    url: str = typer.Option("", help="Ollama URL (default: LOCAL_LLM_URL)"),
) -> None:
    """Run the reply and drafting evaluations on each model (synthetic data only) and recommend the best safe one.
    Nothing is sent or stored. Pull the models first (docs/LOCAL-LLM.md); each one takes minutes on a CPU."""
    from app.evals import benchmark
    from app.llm.factory import local_backend

    settings = get_settings()
    if url:
        settings = settings.model_copy(update={"local_llm_url": url})
    chosen = [s.strip() for s in sets.split(",") if s.strip()]
    if bad := [s for s in chosen if s not in benchmark.SETS]:
        raise typer.BadParameter(f"unknown set(s) {bad}; choose from {sorted(benchmark.SETS)}")
    names = [m.strip() for m in models.split(",") if m.strip()]
    results, best = benchmark.run(names, lambda m: local_backend(settings, m), chosen, drafts)
    out = {
        "results": [{k: v for k, v in r.__dict__.items() if k != "detail"} | {"detail": r.detail} for r in results],
        "recommended": best,
        "how_to_use": f"set LOCAL_LLM_MODEL={best} and restart the api and workers"
        if best
        else "no model was fully safe on these sets: keep the current one and check the details",
    }
    typer.echo(json.dumps(out, indent=2, default=str))


@places_app.command("discover")
def places_discover(
    client: str = typer.Option(..., help="the client's slug (its live switch and the dry-run log apply)"),
    query: str = typer.Option(..., help='activity and area, e.g. "IT support Leeds" or "agence web Lyon"'),
    country: str = typer.Option("GB", help="GB (company number) or FR (SIREN)"),
    pages: int = typer.Option(1, min=1, max=3, help="pages of 20 results; each page is one billed request"),
    add: bool = typer.Option(False, help="add the confirmed companies to the client's watchlist"),
    operator_email: str = typer.Option("", help="with --add: your operator email (recorded on the new version)"),
) -> None:
    """Search Google Places for businesses, keep those whose own website prints a register number that the register
    confirms, and list them with the business address their site publishes. Before go-live (or with FREE_ONLY=true)
    nothing is called: the search is written to the dry-run log."""
    from app.adapters.base import AdapterNotConfigured
    from app.adapters.companies_house.base import CompaniesRegistry
    from app.adapters.website_contacts import WebsiteFinder
    from app.domain.paid.discovery import discover_companies
    from app.domain.paid.places import PROVIDER, usage
    from app.domain.paid.providers import places_for
    from app.domain.tenants.config import load_config
    from app.domain.tenants.onboarding import set_config

    s = get_settings()
    country = country.upper()
    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:
        tenant_id = conn.execute(text("SELECT id FROM tenants WHERE slug = :s"), {"s": client}).scalar()
    if tenant_id is None:
        raise typer.BadParameter(f"no client {client!r}")
    registry: CompaniesRegistry
    if country == "GB":
        from app.adapters.companies_house.live import CompaniesHouseRegistry

        key = s.companies_house_api_key
        if key is None or not key.get_secret_value():
            raise typer.BadParameter("COMPANIES_HOUSE_API_KEY is not set (free)")
        registry = CompaniesHouseRegistry(key.get_secret_value())
    elif country == "FR":
        from app.adapters.registries.france import FranceRegistry

        registry = FranceRegistry()
    else:
        raise typer.BadParameter("country must be GB or FR")
    try:
        places = places_for(
            s.model_copy(update={"places_region": country}), engine, PiiCipher(s.key("pii_master_key")), tenant_id
        )
    except AdapterNotConfigured as exc:
        raise typer.BadParameter(str(exc)) from exc
    finder = WebsiteFinder(contact=s.crawler_contact)
    report = discover_companies(places, finder, registry, query, country=country, pages=pages)
    out: dict[str, object] = {
        "called_google": places.allowed,
        "note": None
        if places.allowed
        else "dry-run: recorded in the dry-run log, nothing called (needs the three "
        "keys: PLACES_ADAPTER_MODE=live, the client live, global go-live signed off)",
        "searched": report.searched,
        "with_website": report.with_website,
        "no_number_on_site": report.no_number_on_site,
        "not_confirmed_by_register": report.not_confirmed_by_register,
        "site_check_failed": report.site_check_failed,
        "found": [f.__dict__ for f in report.found],
        "places_calls_this_month": f"{usage(engine, PROVIDER)}/{s.places_monthly_cap}",
    }
    if add and report.found:
        with transaction(DbContext(role="operator"), engine) as conn:
            op = conn.execute(
                text("SELECT id FROM users WHERE email_bidx = :b AND role = 'operator'"),
                {"b": blind_index(s.key("blind_index_key"), operator_email, None)},
            ).scalar()
            if op is None:
                raise typer.BadParameter("--add needs --operator-email of an existing operator")
            _, cfg = load_config(conn, tenant_id)
            have = {w.company_number for w in cfg.watchlist if w.company_number}
            new = [
                {"name": f.name, "company_number": f.company_number, "domain": f.domain, "country": f.country}
                for f in report.found
                if f.company_number not in have
            ]
            data = cfg.model_dump()
            data["watchlist"] = [*data["watchlist"], *new]
            out["added"] = len(new)
            out["config_version"] = set_config(conn, tenant_id=tenant_id, operator_id=op, config=data) if new else None
    typer.echo(json.dumps(out, indent=2, default=str))
