"""Dry-run 'provider': records the full would-be effect (encrypted) in dry_run_log. Idempotent per key."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.adapters.email.base import EmailPayload, SendResult
from app.core.crypto import PiiCipher, blind_index


def record_email(
    conn: Connection,
    cipher: PiiCipher,
    blind_key: bytes,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    payload: EmailPayload,
) -> SendResult:
    blob = cipher.encrypt(
        conn,
        tenant_id,
        "dry_run_log.payload",
        json.dumps(
            {
                "to": payload.to_email,
                "from": payload.from_address,
                "from_name": payload.from_name,
                "subject": payload.subject,
                "body": payload.body_text,
                "headers": payload.headers,
            }
        ),
    )
    row = conn.execute(
        text("""INSERT INTO dry_run_log (tenant_id, effect_type, adapter, idempotency_key, entity_id, payload_enc,
                  recipient_bidx) VALUES (:t, 'email_send', 'dry_run', :k, :e, :p, :b)
                ON CONFLICT (tenant_id, effect_type, idempotency_key) DO NOTHING RETURNING id"""),
        {
            "t": tenant_id,
            "k": payload.idempotency_key,
            "e": entity_id,
            "p": blob,
            "b": blind_index(blind_key, payload.to_email, tenant_id),
        },
    ).scalar()
    if row is None:  # already recorded by an earlier attempt: same result, no second record
        row = conn.execute(
            text(
                "SELECT id FROM dry_run_log WHERE tenant_id = :t AND effect_type = 'email_send' "
                "AND idempotency_key = :k"
            ),
            {"t": tenant_id, "k": payload.idempotency_key},
        ).scalar_one()
    return SendResult(provider_message_id=f"dryrun-{row}")
