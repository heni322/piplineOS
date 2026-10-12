"""Creating message drafts (encrypted body, recipient hashes for the DB send gate, rendered footer)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.crypto import PiiCipher
from app.domain.suppression.service import RecipientHashes


def create_draft(
    conn: Connection,
    *,
    cipher: PiiCipher,
    suppression_key: bytes,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    campaign_id: uuid.UUID,
    inbox_id: uuid.UUID | None,
    recipient_email: str,
    subject: str,
    body: str,
    footer: str,
    sequence_step: int = 0,
    language: str = "en-GB",
    quality_score: float | None = None,
    prompt_version: str | None = None,
) -> uuid.UUID:
    hashes = RecipientHashes.for_email(suppression_key, recipient_email)
    message_id = uuid.uuid4()
    conn.execute(
        text("""INSERT INTO messages (id, tenant_id, lead_id, campaign_id, inbox_id, sequence_step, language,
          subject_enc, body_enc, compliance_footer, quality_score, prompt_version, recipient_hash,
          recipient_domain_hash, status, idempotency_key)
          VALUES (:id, :t, :l, :c, :i, :step, :lang, :s, :b, :f, :q, :pv, :rh, :dh, 'draft', :k)"""),
        {
            "id": message_id,
            "t": tenant_id,
            "l": lead_id,
            "c": campaign_id,
            "i": inbox_id,
            "step": sequence_step,
            "lang": language,
            "s": cipher.encrypt(conn, tenant_id, "messages.subject", subject),
            "b": cipher.encrypt(conn, tenant_id, "messages.body", body),
            "f": footer,
            "q": quality_score,
            "pv": prompt_version,
            "rh": hashes.email,
            "dh": hashes.domain,
            "k": f"msg:{lead_id}:{sequence_step}",
        },
    )
    return message_id
