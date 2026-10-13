"""Transactional emails to clients (account invites, password resets, invoice notices). Not marketing, so no
prospect compliance gate, but the same three-key switch: dry-run (recorded, encrypted) unless the email adapter
is live, the client is live and global go-live is signed off."""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.adapters.email.base import EmailPayload, SendResult
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.domain.outbound import dry_run
from app.domain.outbound.modes import SendMode, resolve_mode
from app.domain.outbound.providers import email_senders
from app.settings import Settings

Kind = Literal["invite", "password_reset", "invoice_issued"]


def send(
    conn: Connection,
    settings: Settings,
    cipher: PiiCipher,
    *,
    tenant_id: uuid.UUID | None,
    kind: Kind,
    to_email: str,
    subject: str,
    body: str,
    idempotency_key: str,
    entity_id: uuid.UUID,
) -> tuple[SendMode, SendResult]:
    live_tenant = bool(
        tenant_id and conn.execute(text("SELECT live_mode FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar()
    )
    mode = resolve_mode(
        adapter_mode=settings.email_adapter_mode,
        tenant_live=live_tenant,
        global_go_live=settings.global_go_live,
        golive_complete=bool(conn.execute(text("SELECT golive_complete('uk')")).scalar()),
    )
    payload = EmailPayload(
        to_email=to_email,
        from_address=settings.system_mail_from,
        from_name=settings.system_mail_name,
        subject=subject,
        body_text=body,
        idempotency_key=f"system:{idempotency_key}",
        headers={"Auto-Submitted": "auto-generated"},
    )
    if mode == "dry_run" or (tenant_id is None and mode != "sandbox"):
        # Operator accounts have no tenant: their emails stay dry-run until a system-level live switch exists.
        owner = (
            tenant_id
            or conn.execute(text("SELECT nullif(current_setting('app.tenant_id', true), '')::uuid")).scalar()
            or _operator_tenant(conn)
        )
        result = dry_run.record_email(conn, cipher, settings.key("blind_index_key"), owner, entity_id, payload)
        mode = "dry_run"
    else:
        live, sandbox = email_senders(settings)
        sender = live if mode == "live" else sandbox
        if sender is None:
            raise RuntimeError(f"no {mode} email sender configured")
        result = sender.send(payload)
    write_audit(
        conn,
        action=f"system_mail.{kind}.{mode}",
        actor_type="system",
        actor_id="system_mail",
        tenant_id=tenant_id,
        entity_type="system_mail",
        entity_id=entity_id,
    )
    return mode, result


def _operator_tenant(conn: Connection) -> uuid.UUID:
    tid = conn.execute(text("SELECT id FROM tenants ORDER BY is_operator_own DESC, created_at LIMIT 1")).scalar()
    if tid is None:
        raise RuntimeError("no tenant to record a dry-run system email against")
    return tid  # type: ignore[no-any-return]
