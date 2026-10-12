"""Queue-time compliance gate: run after enrichment, before any LLM drafting is paid for."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.crypto import PiiCipher
from app.domain.compliance.engine import ComplianceDecision, ProspectFacts, Stage, evaluate
from app.domain.compliance.repository import build_context, record_decision
from app.domain.leads.state_machine import State, transition
from app.domain.suppression.service import RecipientHashes
from app.domain.suppression.service import check as check_suppression

ALT_TASK_TEXT = (
    "Email is blocked for this prospect ({reasons}). Consider a human-executed channel: engage with their "
    "public professional posts, ask for a warm introduction, or invite them to a relevant event. "
    "No automated messaging."
)


def run_gate(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    cipher: PiiCipher,
    suppression_key: bytes,
    stage: Stage = "queue",
) -> tuple[uuid.UUID, ComplianceDecision]:
    r = (
        conn.execute(
            text("""
        SELECT l.state, l.campaign_id, p.email_enc, p.country_code AS p_country, p.subscriber_type,
               p.email_verification, p.role_relevance, c.country_code AS c_country
        FROM leads l JOIN companies c ON c.id = l.company_id LEFT JOIN prospects p ON p.id = l.prospect_id
        WHERE l.id = :l FOR UPDATE OF l"""),
            {"l": lead_id},
        )
        .mappings()
        .one()
    )
    if r["email_enc"] is not None:
        email = cipher.decrypt(conn, tenant_id, "prospects.email", r["email_enc"])
        suppression = check_suppression(conn, tenant_id, RecipientHashes.for_email(suppression_key, email))
    else:  # no email: nothing to suppress against, but email channel will fail EMAIL_NOT_VERIFIED
        from datetime import UTC, datetime

        from app.domain.compliance.engine import SuppressionResult

        suppression = SuppressionResult(False, False, datetime.now(UTC))
    facts = ProspectFacts(
        r["p_country"],
        r["c_country"],
        r["subscriber_type"] or "unknown",
        r["email_verification"] or "unknown",
        float(r["role_relevance"]) if r["role_relevance"] is not None else None,
    )
    ctx = build_context(
        conn, stage=stage, tenant_id=tenant_id, campaign_id=r["campaign_id"], prospect=facts, suppression=suppression
    )
    decision = evaluate(ctx)
    decision_id = record_decision(conn, tenant_id=tenant_id, lead_id=lead_id, stage=stage, decision=decision)
    reasons = ",".join(x.value for x in decision.reason_codes)
    if decision.suppressed:
        target = State.SUPPRESSED
    elif decision.outcome == "alternative_channel":
        target = State.ALTERNATIVE_CHANNEL
    elif decision.outcome == "block":
        target = State.REJECTED_BY_COMPLIANCE
    else:
        target = State.COMPLIANCE_CHECKED
    transition(conn, lead_id, target, actor_type="system", actor_id="compliance_gate", reason=reasons)
    if target == State.ALTERNATIVE_CHANNEL:
        conn.execute(
            text("""INSERT INTO alternative_channel_tasks (tenant_id, lead_id, channel, instructions)
              VALUES (:t, :l, 'linkedin_manual', :i)"""),
            {"t": tenant_id, "l": lead_id, "i": ALT_TASK_TEXT.format(reasons=reasons)},
        )
    return decision_id, decision
