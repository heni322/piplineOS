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
app.add_typer(keys_app, name="keys")
app.add_typer(config_app, name="config")
app.add_typer(users_app, name="users")
app.add_typer(demo_app, name="demo")


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


if __name__ == "__main__":
    app()
