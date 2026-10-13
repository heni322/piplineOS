"""Client accounts: invite by email with a single-use link (no password ever leaves the system), and password
reset. Tokens are 256-bit random; only their SHA-256 is stored; links expire."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.core.crypto import PiiCipher, blind_index
from app.core.security import hash_password
from app.db.session import DbContext, transaction
from app.domain.outbound import system_mail
from app.settings import Settings

INVITE_TTL = timedelta(hours=72)
RESET_TTL = timedelta(hours=1)


def _token() -> tuple[str, bytes]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).digest()


def token_hash(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def link(settings: Settings, raw: str) -> str:
    return f"{settings.public_base_url}/set-password?token={raw}"


def invite(
    conn: Connection,
    settings: Settings,
    cipher: PiiCipher,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: str,
    invited_by: uuid.UUID,
    now: datetime | None = None,
) -> tuple[uuid.UUID, str]:
    """Creates the client login with an unusable random password and emails a set-password link (dry-run until
    live). Returns (user_id, raw link) — the link is only shown to callers in dev/test."""
    now = now or datetime.now(UTC)
    uid: uuid.UUID = conn.execute(
        text("""INSERT INTO users (tenant_id, email_bidx, email_enc, password_hash, role)
          VALUES (:t, :b, :e, :p, :r) RETURNING id"""),
        {
            "t": tenant_id,
            "b": blind_index(settings.key("blind_index_key"), email, None),
            "e": cipher.encrypt(conn, None, "users.email", email),
            "p": hash_password(secrets.token_urlsafe(32)),
            "r": role,
        },
    ).scalar_one()
    raw, h = _token()
    tid: uuid.UUID = conn.execute(
        text("""INSERT INTO account_tokens (user_id, purpose, token_hash, expires_at, created_by)
          VALUES (:u, 'invite', :h, :x, :by) RETURNING id"""),
        {"u": uid, "h": h, "x": now + INVITE_TTL, "by": invited_by},
    ).scalar_one()
    client = conn.execute(text("SELECT legal_name FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar_one()
    url = link(settings, raw)
    system_mail.send(
        conn,
        settings,
        cipher,
        tenant_id=tenant_id,
        kind="invite",
        to_email=email,
        subject="Your PipelineOS account",
        body=(
            f"Hello,\n\nAn account has been created for you to follow {client}'s results on PipelineOS.\n\n"
            f"Choose your password here (the link works once and expires in 72 hours):\n{url}\n\n"
            "If you weren't expecting this, you can ignore this email."
        ),
        idempotency_key=f"invite:{tid}",
        entity_id=tid,
    )
    return uid, url


def request_reset(
    engine: Engine, settings: Settings, cipher: PiiCipher, *, email: str, now: datetime | None = None
) -> str | None:
    """Always behaves the same for unknown addresses (the caller returns 204 either way). The token is issued
    anonymously through a definer function; the email is then recorded in the account's own tenant context."""
    now = now or datetime.now(UTC)
    raw, h = _token()
    with transaction(DbContext(), engine) as conn:
        row = conn.execute(
            text("SELECT * FROM auth_issue_reset(:b, :h, :x)"),
            {"b": blind_index(settings.key("blind_index_key"), email, None), "h": h, "x": now + RESET_TTL},
        ).first()
    if row is None:
        return None
    url = link(settings, raw)
    tenant_id = row.tenant_id
    if tenant_id is None:  # operators have no tenant; file the dry-run record under the operator's own tenant
        with transaction(DbContext.system(), engine) as conn:
            tenant_id = system_mail._operator_tenant(conn)
    with transaction(DbContext.system(tenant_id), engine) as conn:
        system_mail.send(
            conn,
            settings,
            cipher,
            tenant_id=row.tenant_id and tenant_id,
            kind="password_reset",
            to_email=email,
            subject="Reset your PipelineOS password",
            body=(
                f"Hello,\n\nUse this link to choose a new password. It works once and expires in one hour:\n"
                f"{url}\n\nIf you didn't ask for this, ignore this email; your password hasn't changed."
            ),
            idempotency_key=f"reset:{h.hex()[:24]}",
            entity_id=row.user_id,
        )
    return url


def set_password(conn: Connection, *, raw_token: str, password: str) -> uuid.UUID | None:
    return conn.execute(
        text("SELECT auth_set_password(:h, :p)"), {"h": token_hash(raw_token), "p": hash_password(password)}
    ).scalar()
