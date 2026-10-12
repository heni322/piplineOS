"""Phase 4 demo: approve the drafts, dry-run send, then simulate prospects replying (synthetic) and let Agent 4
handle them: qualification -> slot offer -> booking -> confirmation -> reminders -> attendance, plus out-of-office,
referral, unsubscribe and a data-protection request. DEV/TEST DATABASES ONLY; time is simulated."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.inbound.base import InboundEmail
from app.agents.base import AgentContext
from app.core.crypto import PiiCipher
from app.db.session import DbContext, transaction
from app.domain.approvals import service as approvals
from app.domain.meetings import service as meetings
from app.domain.outbound.calendar import CalendarGateway
from app.workflows.pipeline import Providers, process_inbound, send_due

FIRST_REPLIES = {
    "Contoso Managed IT Ltd": (
        "Hi Jordan, thanks, the timing is good actually. Our team is stretched out of hours and the "
        "ticket backlog grows every weekend. We'd like to sort cover before Christmas. Happy to talk."
    ),
    "Adventure Works MSP Ltd": (
        "Automatic reply: I am out of the office on annual leave until 2 November 2026 with limited access to email."
    ),
    "Woodgrove IT plc": (
        "Thanks Jordan, I'm not the right person for this. Please speak to Sam Okafor "
        "(sam.okafor@woodgrove-it.example), our Head of Service Delivery."
    ),
    "Proseware Systems Ltd": "Please remove me from your mailing list.",
    "Blue Yonder Managed Services Ltd": (
        "Under GDPR, please tell me what personal data you hold about me and where you got my details."
    ),
}


@dataclass
class ConversationRun:
    outcomes: dict[str, list[str]] = field(default_factory=dict)
    meeting_id: uuid.UUID | None = None
    final_states: dict[str, str] = field(default_factory=dict)


def _approve_all(engine: Engine, cipher: PiiCipher, tenant_id: uuid.UUID, operator_id: uuid.UUID) -> int:
    with transaction(DbContext(role="operator", user_id=operator_id), engine) as conn:
        heads = (
            conn.execute(
                text(
                    "SELECT id FROM messages WHERE tenant_id = :t AND status = 'pending_approval' AND "
                    "sequence_step = 0 ORDER BY created_at"
                ),
                {"t": tenant_id},
            )
            .scalars()
            .all()
        )
        for h in heads:
            approvals.decide(
                conn,
                cipher,
                tenant_id=tenant_id,
                head_message_id=h,
                reviewer_id=operator_id,
                decision="approve",
                edits=[],
                reason=None,
                review_seconds=20,
            )
    return len(heads)


def _reply(
    engine: Engine, tenant_id: uuid.UUID, company: str, body: str, at: datetime, kind: str = "outreach"
) -> InboundEmail:
    with transaction(DbContext.system(tenant_id), engine) as conn:
        row = conn.execute(
            text("""SELECT m.provider_message_id FROM messages m JOIN leads l ON l.id = m.lead_id
              JOIN companies c ON c.id = l.company_id WHERE c.name = :n AND m.kind = :k
              AND m.provider_message_id IS NOT NULL
              ORDER BY m.sent_at DESC LIMIT 1"""),
            {"n": company, "k": kind},
        ).scalar_one()
    return InboundEmail(
        provider_message_id=f"<{uuid.uuid4()}@prospect.example>",
        from_email="unused@example.test",
        subject="Re: our conversation",
        body_text=body,
        received_at=at,
        in_reply_to=row,
    )


def run_conversations(
    ctx: AgentContext,
    providers: Providers,
    operator_id: uuid.UUID,
    start: datetime = datetime(2026, 10, 13, 9, 0, tzinfo=UTC),
) -> ConversationRun:
    engine, settings, cipher, t = ctx.engine, ctx.settings, ctx.cipher, ctx.tenant_id
    cal = CalendarGateway(settings, cipher)
    run = ConversationRun()
    _approve_all(engine, cipher, t, operator_id)
    send_due(engine, settings, cipher, t, now=start)  # Tue 10:00 UK: openers (dry-run)

    with transaction(DbContext.system(t), engine) as conn:  # sender address per lead
        emails = {
            r.name: cipher.decrypt(conn, t, "prospects.email", r.email_enc)
            for r in conn.execute(
                text(
                    "SELECT c.name, p.email_enc FROM leads l JOIN companies c ON c.id = l.company_id JOIN prospects p "
                    "ON p.id = l.prospect_id WHERE l.state = 'contacted'"
                )
            )
        }
    msgs = []
    for company, body in FIRST_REPLIES.items():
        if company in emails:
            m = _reply(engine, t, company, body, start + timedelta(hours=2))
            msgs.append(
                InboundEmail(
                    m.provider_message_id, emails[company], m.subject, m.body_text, m.received_at, m.in_reply_to
                )
            )
    for company, out in zip(
        [c for c in FIRST_REPLIES if c in emails],
        process_inbound(ctx, msgs, providers, cal, now=start + timedelta(hours=2)),
        strict=True,
    ):
        run.outcomes[company] = out.actions

    # Contoso: the slot offer is approved and sent; the prospect picks option 2; booking + confirmation.
    if "Contoso Managed IT Ltd" in emails:
        _approve_all(engine, cipher, t, operator_id)
        send_due(engine, settings, cipher, t, now=start + timedelta(hours=3))
        m = _reply(
            engine,
            t,
            "Contoso Managed IT Ltd",
            "Option 2 works for me, thanks.",
            start + timedelta(hours=4),
            kind="agent_reply",
        )
        pick = process_inbound(
            ctx,
            [
                InboundEmail(
                    m.provider_message_id,
                    emails["Contoso Managed IT Ltd"],
                    m.subject,
                    m.body_text,
                    m.received_at,
                    m.in_reply_to,
                )
            ],
            providers,
            cal,
            now=start + timedelta(hours=4),
        )[0]
        run.outcomes["Contoso Managed IT Ltd (slot choice)"] = pick.actions
        send_due(engine, settings, cipher, t, now=start + timedelta(hours=4, minutes=5))  # confirmation (dry-run)
        with transaction(DbContext.system(t), engine) as conn:
            mt = conn.execute(
                text("SELECT id, starts_at, ends_at FROM meetings ORDER BY created_at DESC LIMIT 1")
            ).first()
            if mt:
                run.meeting_id = mt.id
                meetings.confirm(conn, t, mt.id)  # prospect accepts the invite
        if mt:
            send_due(engine, settings, cipher, t, now=mt.starts_at - timedelta(minutes=1430))  # 24h reminder
            send_due(engine, settings, cipher, t, now=mt.starts_at - timedelta(minutes=55))  # 1h reminder
            with transaction(DbContext.system(t), engine) as conn:
                meetings.record_attendance(
                    conn,
                    tenant_id=t,
                    meeting_id=mt.id,
                    status="attended",
                    source="calendar",
                    user_id=None,
                    now=mt.ends_at + timedelta(minutes=5),
                )
    with transaction(DbContext.system(t), engine) as conn:
        run.final_states = {
            r.n: r.state
            for r in conn.execute(
                text(
                    "SELECT coalesce(c.name, '[sole trader]') || CASE WHEN l.referred_from_lead_id IS NOT NULL "
                    "THEN ' (referral)' "
                    "ELSE '' END AS n, l.state FROM leads l JOIN companies c ON c.id = l.company_id"
                )
            )
        }
    return run


def summary(run: ConversationRun) -> dict[str, Any]:
    return {
        "reply_handling": run.outcomes,
        "final_states": run.final_states,
        "meeting_id": str(run.meeting_id) if run.meeting_id else None,
    }
