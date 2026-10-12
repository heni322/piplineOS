"""Data-protection requests (UK GDPR / EU GDPR): logged with the one-month deadline and regime, a drafted response
for a HUMAN to send from the controller's mailbox (never auto-sent: the outbound gateway refuses suppressed
recipients by design), escalation to the tenant contact, and an erasure procedure run after identity checks."""

from __future__ import annotations

import calendar
import contextlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.domain.leads.state_machine import State, TransitionError, transition

EU_EEA = frozenset(
    [
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
        "IS",
        "LI",
        "NO",
    ]
)
REQUEST_TYPE = {"unsubscribe": "unsubscribe", "objection": "objection"}

RESPONSES = {
    "unsubscribe": (
        "Thank you for your message. We have removed your details from our outreach and you will not "
        "receive further marketing emails from us. We keep only a scrambled record so that you are never "
        "contacted again."
    ),
    "objection": (
        "Thank you for letting us know. We have recorded your objection and stopped all contact. We keep "
        "only a scrambled record so that you are not contacted again."
    ),
    "access": (
        "Thank you for your request. We will confirm what personal data we hold about you, where it came from "
        "and how we use it within one month. We may ask you to verify your identity first."
    ),
    "erasure": (
        "Thank you for your request. We have stopped all contact and will erase your personal data within one "
        "month, keeping only a scrambled record so that you are not contacted again. We may ask you to verify "
        "your identity first."
    ),
    "rectification": "Thank you. We will correct your details within one month and confirm once done.",
    "restriction": "Thank you. We have restricted the use of your data and stopped all contact while we review this.",
    "portability": "Thank you. We will send you a copy of your data in a portable format within one month.",
    "unclear": (
        "Thank you for your message about your personal data. We have stopped all contact. Could you tell us "
        "which right you would like to exercise (for example access or erasure)? We will respond within one month."
    ),
}


def add_one_calendar_month(dt: datetime) -> datetime:
    """Same day next month; if that day does not exist, the last day of next month (ICO calculation)."""
    y, m = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))


def regime_for(prospect_country: str | None, company_country: str | None) -> str:
    cs = {c for c in (prospect_country, company_country) if c}
    uk, eu = "GB" in cs, bool(cs & EU_EEA)
    if not cs or (uk and eu):
        return "both"
    return "eu_gdpr" if eu else "uk_gdpr"


def open_request(
    conn: Connection,
    cipher: PiiCipher,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID | None,
    prospect_ref_hash: bytes,
    request_type: str,
    prospect_country: str | None,
    company_country: str | None,
    received_at: datetime,
    reply_id: uuid.UUID | None,
    matched_rules: list[str],
    controller: str,
) -> uuid.UUID:
    rtype = request_type if request_type in RESPONSES else "unclear"
    draft = RESPONSES[rtype]
    dpr_id: uuid.UUID = conn.execute(
        text("""INSERT INTO data_protection_requests (tenant_id, lead_id, prospect_ref_hash,
          request_type, regime, controller, received_at, deadline_at, response_draft_enc, details, reply_id)
          VALUES (:t, :l, :h, :rt, :rg, :c, :at, :dl, :d, CAST(:det AS jsonb), :r) RETURNING id"""),
        {
            "t": tenant_id,
            "l": lead_id,
            "h": prospect_ref_hash,
            "rt": rtype,
            "rg": regime_for(prospect_country, company_country),
            "c": controller,
            "at": received_at,
            "dl": add_one_calendar_month(received_at),
            "d": cipher.encrypt(conn, tenant_id, "dpr.response_draft", draft),
            "det": json.dumps({"matched_rules": matched_rules}),
            "r": reply_id,
        },
    ).scalar_one()
    write_audit(
        conn,
        action="dpr.opened",
        actor_type="system",
        actor_id="reply_handler",
        tenant_id=tenant_id,
        entity_type="data_protection_request",
        entity_id=dpr_id,
        after={"type": rtype},
    )
    return dpr_id


def complete_erasure(
    conn: Connection, *, tenant_id: uuid.UUID, dpr_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, int]:
    """Erase/anonymise the prospect everywhere in this tenant. Keeps only: the hashed suppression entry, anonymised
    lead/prospect stubs for aggregate metrics, and the audit trail (PII-free). Run after verifying identity."""
    req = (
        conn.execute(
            text("SELECT lead_id, status, request_type FROM data_protection_requests WHERE id = :d FOR UPDATE"),
            {"d": dpr_id},
        )
        .mappings()
        .one()
    )
    if req["lead_id"] is None:
        raise ValueError("request is not linked to a lead")
    lead = conn.execute(text("SELECT prospect_id, company_id FROM leads WHERE id = :l"), {"l": req["lead_id"]}).one()
    pid = lead.prospect_id
    lead_ids = conn.execute(text("SELECT id FROM leads WHERE prospect_id = :p"), {"p": pid}).scalars().all()
    counts: dict[str, int] = {}

    def run(name: str, sql: str, params: dict[str, object]) -> None:
        counts[name] = conn.execute(text(sql), params).rowcount

    run(
        "prospect",
        """UPDATE prospects SET name_enc = NULL, email_enc = NULL, email_bidx = NULL, profile_url_enc = NULL,
        title = NULL, anonymised_at = now() WHERE id = :p""",
        {"p": pid},
    )
    run("field_sources", "DELETE FROM prospect_field_sources WHERE prospect_id = :p", {"p": pid})
    run(
        "messages",
        "UPDATE messages SET subject_enc = NULL, body_enc = NULL, subject_options_enc = NULL WHERE lead_id = ANY(:ls)",
        {"ls": list(lead_ids)},
    )
    run(
        "claims",
        "UPDATE message_personalization_sources SET claim_text = '[erased]' WHERE message_id IN "
        "(SELECT id FROM messages WHERE lead_id = ANY(:ls))",
        {"ls": list(lead_ids)},
    )
    run(
        "approvals",
        "UPDATE approvals SET original_body_enc = NULL, edited_body_enc = NULL, original_subject_enc = NULL, "
        "edited_subject_enc = NULL, diff = NULL WHERE message_id IN (SELECT id FROM messages WHERE lead_id = ANY(:ls))",
        {"ls": list(lead_ids)},
    )
    run(
        "replies",
        "UPDATE replies SET raw_enc = NULL, body_text_enc = NULL, subject_enc = NULL, from_bidx = NULL, "
        "extracted_referral = NULL WHERE lead_id = ANY(:ls)",
        {"ls": list(lead_ids)},
    )
    run("meetings", "UPDATE meetings SET brief_enc = NULL WHERE lead_id = ANY(:ls)", {"ls": list(lead_ids)})
    run(
        "dry_run_log",
        "UPDATE dry_run_log SET payload_enc = NULL, recipient_bidx = NULL WHERE entity_id IN "
        "(SELECT id FROM messages WHERE lead_id = ANY(:ls) UNION SELECT id FROM meetings WHERE lead_id = ANY(:ls))",
        {"ls": list(lead_ids)},
    )
    run(
        "llm_calls",
        "UPDATE llm_calls SET request_enc = NULL, response_enc = NULL WHERE lead_id = ANY(:ls)",
        {"ls": list(lead_ids)},
    )
    run(
        "company",
        """UPDATE companies SET name = '[erased sole trader]', name_enc = NULL, name_is_personal = false
        WHERE id = :c AND name_is_personal""",
        {"c": lead.company_id},
    )
    for lid in lead_ids:
        with contextlib.suppress(TransitionError):
            transition(
                conn, lid, State.ERASED, actor_type="user", actor_id=str(user_id), reason="erasure request completed"
            )
    conn.execute(
        text(
            "UPDATE data_protection_requests SET status = 'completed', completed_at = now(), responded_at = "
            "coalesce(responded_at, now()), handled_by = :u, response_draft_enc = NULL WHERE id = :d"
        ),
        {"u": user_id, "d": dpr_id},
    )
    write_audit(
        conn,
        action="dpr.erasure_completed",
        actor_type="user",
        actor_id=str(user_id),
        tenant_id=tenant_id,
        entity_type="data_protection_request",
        entity_id=dpr_id,
        after=counts,
    )
    return counts


def days_remaining(deadline: datetime, now: datetime | None = None) -> int:
    return (deadline - (now or datetime.now(UTC))).days
