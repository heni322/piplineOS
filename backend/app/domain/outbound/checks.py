"""Send-time compliance + suppression re-check for non-email side effects (calendar bookings)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.crypto import PiiCipher
from app.domain.compliance.engine import ComplianceDecision, ProspectFacts, evaluate
from app.domain.compliance.repository import build_context, record_decision
from app.domain.suppression.service import RecipientHashes
from app.domain.suppression.service import check as check_suppression


def recheck_lead(
    conn: Connection, cipher: PiiCipher, suppression_key: bytes, tenant_id: uuid.UUID, lead_id: uuid.UUID
) -> tuple[uuid.UUID, ComplianceDecision, str]:
    r = (
        conn.execute(
            text("""SELECT l.campaign_id, p.email_enc, p.country_code AS pc, p.subscriber_type,
          p.email_verification, p.role_relevance, c.country_code AS cc
          FROM leads l JOIN prospects p ON p.id = l.prospect_id JOIN companies c ON c.id = l.company_id
          WHERE l.id = :l"""),
            {"l": lead_id},
        )
        .mappings()
        .one()
    )
    email = cipher.decrypt(conn, tenant_id, "prospects.email", r["email_enc"])
    sup = check_suppression(conn, tenant_id, RecipientHashes.for_email(suppression_key, email))
    facts = ProspectFacts(
        r["pc"],
        r["cc"],
        r["subscriber_type"],
        r["email_verification"],
        float(r["role_relevance"]) if r["role_relevance"] is not None else None,
    )
    decision = evaluate(
        build_context(
            conn, stage="send", tenant_id=tenant_id, campaign_id=r["campaign_id"], prospect=facts, suppression=sup
        )
    )
    did = record_decision(conn, tenant_id=tenant_id, lead_id=lead_id, stage="send", decision=decision)
    return did, decision, email
