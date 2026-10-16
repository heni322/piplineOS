"""Inbound replies: idempotent ingestion -> deterministic safety rules -> Agent 4 -> actions.

Actions per class (docs/04-state-machine.md):
- unsubscribe / objection / data_protection_request: global suppression NOW (same transaction as the reply),
  stop all follow-ups, DP request record with one-month deadline + regime, drafted response for a human,
  escalation to the tenant contact. Objection after booking keeps the meeting (flagged) but stops everything else.
- out_of_office: pause until the return date + 1 business day; follow-ups move accordingly.
- referral: new lead for the referred person with full enrichment + compliance re-run; original lead closed.
- interested: qualification with verbatim evidence -> slot offer -> booking (re-offer if taken) -> confirmation.
- not_now: courteous acknowledgement for approval, pause for the nurture period.
- negative: tenant-level suppression, lead disqualified.
- unclear / low confidence / anger / legal / pricing / contract / complaint / injection / off-playbook /
  unsupported language: escalate to a human (safety actions above still happen first).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import RowMapping, text
from sqlalchemy.engine import Connection

from app.adapters.inbound.base import InboundEmail
from app.agents.base import AgentContext
from app.agents.decision_maker.agent import role_relevance
from app.agents.reply_handler import agent as agent4
from app.agents.reply_handler.rules import RuleResult, apply_rules, parse_slot_choice
from app.agents.reply_handler.schemas import SAFETY_CLASSES, HandledReply
from app.core import i18n
from app.core.audit import write_audit
from app.core.crypto import blind_index, email_domain, normalise_email, suppression_hash
from app.core.logging import get_logger
from app.db.session import DbContext, transaction
from app.domain.compliance.gate import run_gate
from app.domain.compliance.jurisdiction import parse_location
from app.domain.compliance.subscriber import classify_subscriber
from app.domain.deliverability import killswitch
from app.domain.deliverability.hours import add_business_days
from app.domain.dpr import service as dpr
from app.domain.leads.state_machine import TERMINAL, State, TransitionError, get_lead, record_objection, transition
from app.domain.meetings import service as meetings
from app.domain.outbound.calendar import CalendarGateway
from app.domain.outbound.messages import create_draft
from app.domain.outbound.tenant_footer import tenant_footer
from app.domain.suppression.service import RecipientHashes, suppress_email
from app.domain.tenants import controls
from app.llm.client import LLMEscalation

if TYPE_CHECKING:
    from app.workflows.pipeline import Providers

log = get_logger(__name__)
ESCALATION_REASON = {
    "anger": "anger",
    "legal": "legal",
    "pricing": "pricing",
    "contract": "contract",
    "complaint": "complaint",
    "low_confidence": "low_confidence",
    "unclear": "low_confidence",
    "off_playbook": "off_playbook",
    "prompt_injection": "prompt_injection",
    "unsupported_language": "unsupported_language",
    "llm_failure": "llm_failure",
    "llm_invalid_output": "llm_failure",
    "llm_refusal": "llm_failure",
    "llm_timeout": "llm_failure",
    "llm_budget_exceeded": "llm_failure",
    "calendar_disconnected": "calendar_disconnected",
}


@dataclass
class Outcome:
    reply_id: uuid.UUID | None
    classification: str
    actions: list[str] = field(default_factory=list)
    lead_state: str | None = None
    new_lead_id: uuid.UUID | None = None  # referral: the referred person's lead (drafted after commit)


def message_hash(key: bytes, message_id: str, tenant_id: uuid.UUID) -> bytes:
    """Keyed hash of a Message-ID, per tenant: lets us recognise a message we already handled without storing it."""
    return hmac.new(key, tenant_id.bytes + b"msgid:" + message_id.strip().encode(), hashlib.sha256).digest()


def ingest(conn: Connection, ctx: AgentContext, msg: InboundEmail) -> tuple[uuid.UUID | None, uuid.UUID | None, bool]:
    """Stores the reply (encrypted) once. Returns (reply_id, lead_id, is_new). Unknown senders are not stored, but
    any unsubscribe / DP request from them is still honoured (suppression by hash)."""
    dup = conn.execute(
        text("SELECT id, lead_id FROM replies WHERE provider_message_id = :p"), {"p": msg.provider_message_id}
    ).first()
    if dup:
        return dup.id, dup.lead_id, False
    lead_id = None
    if msg.in_reply_to:
        lead_id = conn.execute(
            text("SELECT lead_id FROM messages WHERE provider_message_id = :p"), {"p": msg.in_reply_to}
        ).scalar()
    bidx = blind_index(ctx.settings.key("blind_index_key"), msg.from_email, ctx.tenant_id)
    if lead_id is None:
        lead_id = conn.execute(
            text(
                "SELECT l.id FROM leads l JOIN prospects p ON p.id = l.prospect_id WHERE "
                "p.email_bidx = :b ORDER BY l.created_at DESC LIMIT 1"
            ),
            {"b": bidx},
        ).scalar()
    if lead_id is None:
        first_time = conn.execute(
            text("""INSERT INTO inbound_seen (tenant_id, message_hash) VALUES (:t, :h)
                    ON CONFLICT DO NOTHING RETURNING 1"""),
            {
                "t": ctx.tenant_id,
                "h": message_hash(ctx.settings.key("blind_index_key"), msg.provider_message_id, ctx.tenant_id),
            },
        ).first()
        if first_time is None:  # the same unknown-sender message again (duplicate push, or the safety sweep)
            return None, None, False
        rules = apply_rules(msg.body_text, msg.received_at.date(), msg.from_email)
        if rules.safety:
            hashes = RecipientHashes.for_email(ctx.settings.key("suppression_hash_key"), msg.from_email)
            suppress_email(
                conn,
                hashes,
                "data_protection_request" if rules.safety == "data_protection_request" else "unsubscribe",
                source_ref="inbound from unknown sender",
            )
            dpr.open_request(
                conn,
                ctx.cipher,
                tenant_id=ctx.tenant_id,
                lead_id=None,
                prospect_ref_hash=hashes.email,
                request_type=rules.dp_type or rules.safety,
                prospect_country=None,
                company_country=None,
                received_at=msg.received_at,
                reply_id=None,
                matched_rules=rules.matched,
                controller="tenant",
            )
        write_audit(
            conn,
            action="reply.unmatched",
            actor_type="system",
            actor_id="inbound",
            tenant_id=ctx.tenant_id,
            after={"safety": rules.safety},
        )
        return None, None, True
    message_id = (
        conn.execute(text("SELECT id FROM messages WHERE provider_message_id = :p"), {"p": msg.in_reply_to}).scalar()
        if msg.in_reply_to
        else None
    )
    reply_id: uuid.UUID = conn.execute(
        text("""INSERT INTO replies (tenant_id, lead_id, message_id, received_at, from_bidx,
          raw_enc, body_text_enc, subject_enc, provider_message_id, in_reply_to)
          VALUES (:t, :l, :m, :at, :b, :raw, :body, :subj, :pid, :irt) RETURNING id"""),
        {
            "t": ctx.tenant_id,
            "l": lead_id,
            "m": message_id,
            "at": msg.received_at,
            "b": bidx,
            "raw": ctx.cipher.encrypt(
                conn,
                ctx.tenant_id,
                "replies.raw",
                json.dumps({"from": msg.from_email, "subject": msg.subject, "body": msg.body_text}),
            ),
            "body": ctx.cipher.encrypt(conn, ctx.tenant_id, "replies.body", msg.body_text),
            "subj": ctx.cipher.encrypt(conn, ctx.tenant_id, "replies.subject", msg.subject or ""),
            "pid": msg.provider_message_id,
            "irt": msg.in_reply_to,
        },
    ).scalar_one()
    return reply_id, lead_id, True


def _escalate(
    conn: Connection, tenant_id: uuid.UUID, lead_id: uuid.UUID, reason: str, detail: dict[str, object]
) -> None:
    lead = get_lead(conn, lead_id, for_update=True)
    conn.execute(
        text("""INSERT INTO escalations (tenant_id, lead_id, reason, context, pre_escalation_state)
          VALUES (:t, :l, :r, CAST(:c AS jsonb), :p)"""),
        {
            "t": tenant_id,
            "l": lead_id,
            "r": ESCALATION_REASON.get(reason, "other"),
            "c": json.dumps(detail),
            "p": lead.state.value,
        },
    )
    if lead.state not in TERMINAL and lead.state != State.ESCALATED:
        # e.g. booked leads keep their meeting state; the escalation row is what the operator works from
        with contextlib.suppress(TransitionError):
            transition(conn, lead_id, State.ESCALATED, actor_type="agent", actor_id="reply_handler", reason=reason)


def _ai_reply(
    conn: Connection,
    ctx: AgentContext,
    *,
    lead_id: uuid.UUID,
    reply_id: uuid.UUID,
    body: str,
    email: str,
    subject: str,
    asks_if_ai: bool,
) -> uuid.UUID:
    cfg = ctx.config
    if cfg.ai_disclosure == "always" or asks_if_ai:
        body = body.rstrip() + "\n\n" + cfg.ai_disclosure_text
    campaign_id, inbox_id = conn.execute(
        text(
            "SELECT l.campaign_id, (SELECT id FROM inboxes WHERE status IN ('active',"
            "'warming') ORDER BY created_at LIMIT 1) FROM leads l WHERE l.id = :l"
        ),
        {"l": lead_id},
    ).one()
    mid = create_draft(
        conn,
        cipher=ctx.cipher,
        suppression_key=ctx.settings.key("suppression_hash_key"),
        tenant_id=ctx.tenant_id,
        lead_id=lead_id,
        campaign_id=campaign_id,
        inbox_id=inbox_id,
        recipient_email=email,
        subject=subject,
        body=body,
        footer=tenant_footer(conn, ctx.tenant_id, cfg, ctx.settings.public_base_url),
        language=cfg.language,
        prompt_version="reply_handler.respond.v1",
        idempotency_key=f"reply:{reply_id}",
        kind="agent_reply",
    )
    conn.execute(
        text(
            "UPDATE messages SET kind = 'agent_reply', status = 'pending_approval', reply_id = :r, "
            "idempotency_key = :k WHERE id = :m"
        ),
        {"r": reply_id, "k": f"reply:{reply_id}", "m": mid},
    )
    return mid


def handle(
    ctx: AgentContext,
    msg: InboundEmail,
    *,
    providers: Providers | None = None,
    calendar: CalendarGateway | None = None,
    now: datetime | None = None,
) -> Outcome:
    at = now or datetime.now(UTC)
    cal = calendar or CalendarGateway(ctx.settings, ctx.cipher)
    cfg = ctx.config
    if killswitch.is_bounce(msg):  # delivery reports never reach Agent 4 (no tokens spent, no misclassification)
        with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
            actions = killswitch.handle_bounce(
                conn, ctx.settings.key("suppression_hash_key"), ctx.cipher, ctx.tenant_id, msg
            )
        return Outcome(None, "bounce", actions)
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        reply_id, lead_id, is_new = ingest(conn, ctx, msg)
        if reply_id is None or lead_id is None:
            return Outcome(None, "unmatched", ["stored_nothing", "safety_rules_applied"])
        if not is_new and conn.execute(text("SELECT handled_at FROM replies WHERE id = :r"), {"r": reply_id}).scalar():
            return Outcome(reply_id, "duplicate", ["ignored_duplicate"])
        lead = get_lead(conn, lead_id, for_update=True)
        if lead.state == State.CONTACTED:
            transition(conn, lead_id, State.REPLIED, actor_type="system", actor_id="inbound", reason="reply received")
        offer = meetings.open_offer(conn, lead_id, at)
        prospect = (
            conn.execute(
                text("""SELECT p.email_enc, p.name_enc, p.title, p.country_code, p.role_relevance,
              c.country_code AS cc, c.domain_normalised, c.name AS company FROM leads l JOIN prospects p
              ON p.id = l.prospect_id JOIN companies c ON c.id = l.company_id WHERE l.id = :l"""),
                {"l": lead_id},
            )
            .mappings()
            .one()
        )
        email = ctx.cipher.decrypt(conn, ctx.tenant_id, "prospects.email", prospect["email_enc"])

    rules = apply_rules(msg.body_text, msg.received_at.date(), msg.from_email)
    labels = [s["label"] for s in offer["slots"]] if offer else []
    decision: HandledReply = agent4.classify(
        ctx, lead_id, msg.body_text, rules, offered_slots=labels, received=msg.received_at.date()
    )
    out = Outcome(reply_id, decision.classification)

    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        cls = decision.classification
        conn.execute(
            text("""UPDATE replies SET classification = :c, confidence = :cf, detected_language = :lg,
              rule_flags = :rf, ooo_return_date = :ooo, classified_at = now(),
              prompt_version = 'reply_handler.classify.v1'
              WHERE id = :r"""),
            {
                "c": cls,
                "cf": decision.confidence,
                "lg": decision.language,
                "rf": rules.matched,
                "ooo": (
                    decision.llm.ooo_return_date
                    if decision.llm and decision.llm.ooo_return_date
                    else rules.ooo_return_date
                ),
                "r": reply_id,
            },
        )

        # ---- 1. safety first: suppression in this same transaction
        if cls in SAFETY_CLASSES:
            skey = ctx.settings.key("suppression_hash_key")
            hashes = RecipientHashes.for_email(skey, email)
            reason = (
                "data_protection_request"
                if cls == "data_protection_request"
                else ("objection" if cls == "objection" else "unsubscribe")
            )
            suppress_email(conn, hashes, reason, source_ref=f"reply:{reply_id}")  # type: ignore[arg-type]
            if normalise_email(msg.from_email) != normalise_email(email):  # replying from another address
                suppress_email(
                    conn,
                    RecipientHashes.for_email(skey, msg.from_email),
                    reason,  # type: ignore[arg-type]
                    source_ref=f"reply:{reply_id}:sender",
                )
            record_objection(conn, lead_id, actor_type="agent", actor_id="reply_handler", reason=f"reply: {cls}")
            conn.execute(
                text("UPDATE slot_offers SET status = 'superseded' WHERE lead_id = :l AND status = 'open'"),
                {"l": lead_id},
            )
            rtype = (
                (decision.llm and decision.llm.classification == "data_protection_request" and rules.dp_type)
                or rules.dp_type
                or dpr.REQUEST_TYPE.get(cls, "unclear")
            )
            controller = (
                "operator"
                if conn.execute(
                    text("SELECT is_operator_own FROM tenants WHERE id = :t"), {"t": ctx.tenant_id}
                ).scalar()
                else "tenant"
            )
            dpr_id = dpr.open_request(
                conn,
                ctx.cipher,
                tenant_id=ctx.tenant_id,
                lead_id=lead_id,
                prospect_ref_hash=suppression_hash(skey, email, "email"),
                request_type=rtype,
                prospect_country=prospect["country_code"],
                company_country=prospect["cc"],
                received_at=msg.received_at,
                reply_id=reply_id,
                matched_rules=rules.matched,
                controller=controller,
            )
            if cls in ("unsubscribe", "objection"):
                # An objection to direct marketing is absolute and fully actioned by the suppression above, so the
                # request is recorded as completed (audit trail) and does not need a person.
                conn.execute(
                    text(
                        "UPDATE data_protection_requests SET status = 'completed', completed_at = now(), "
                        "responded_at = now() WHERE id = :d"
                    ),
                    {"d": dpr_id},
                )
                out.actions += ["suppressed_globally", "follow_ups_cancelled", f"dpr_recorded_completed:{rtype}"]
                serious = [f for f in decision.escalate_reasons if f in ("legal", "complaint", "anger")]
                if serious:  # e.g. "unsubscribe, I'm reporting you to the ICO": a person must still see it
                    _escalate(conn, ctx.tenant_id, lead_id, serious[0], {"reasons": serious, "reply_id": str(reply_id)})
                    out.actions.append(f"escalated:{serious[0]}")
                return _finish(conn, ctx, out, reply_id, lead_id)
            conn.execute(
                text("""INSERT INTO escalations (tenant_id, lead_id, reason, context, pre_escalation_state)
                  VALUES (:t, :l, 'data_protection', CAST(:c AS jsonb), :p)"""),
                {
                    "t": ctx.tenant_id,
                    "l": lead_id,
                    "c": json.dumps({"dpr_id": str(dpr_id), "type": rtype, "tenant_contact_notified": True}),
                    "p": get_lead(conn, lead_id).state.value,
                },
            )
            out.actions += ["suppressed_globally", "follow_ups_cancelled", f"dpr_opened:{rtype}", "tenant_notified"]
            return _finish(conn, ctx, out, reply_id, lead_id)

        # ---- 2. escalation triggers (after safety)
        blocking = [r for r in decision.escalate_reasons if r in ESCALATION_REASON]
        if cls == "out_of_office":
            blocking = [r for r in blocking if r not in ("low_confidence", "unclear")]
        if blocking:
            _escalate(conn, ctx.tenant_id, lead_id, blocking[0], {"reasons": blocking, "reply_id": str(reply_id)})
            out.actions.append(f"escalated:{blocking[0]}")
            return _finish(conn, ctx, out, reply_id, lead_id)

        first_name = ctx.cipher.decrypt(conn, ctx.tenant_id, "prospects.name", prospect["name_enc"]).split()[0]
        llm = decision.llm
        state = get_lead(conn, lead_id).state

        if cls == "out_of_office":
            ret = (llm.ooo_return_date if llm and llm.ooo_return_date else None) or rules.ooo_return_date
            back = datetime.combine(ret, datetime.min.time(), tzinfo=UTC) if ret else at + timedelta(days=7)
            resume = add_business_days(back, 1, prospect["country_code"] or "GB")
            conn.execute(text("UPDATE leads SET paused_until = :p WHERE id = :l"), {"p": resume, "l": lead_id})
            conn.execute(
                text(
                    "UPDATE messages SET scheduled_for = GREATEST(coalesce(scheduled_for, :p), :p) WHERE lead_id = :l "
                    "AND kind = 'outreach' AND status IN ('approved','queued')"
                ),
                {"p": resume, "l": lead_id},
            )
            if state == State.REPLIED:
                transition(
                    conn,
                    lead_id,
                    State.CONTACTED,
                    actor_type="agent",
                    actor_id="reply_handler",
                    reason=f"out of office until {ret or 'unknown (default 7 days)'}",
                )
            out.actions.append(f"paused_until:{resume.date().isoformat()}")

        elif cls == "referral":
            actions, out.new_lead_id = _referral(conn, ctx, lead_id, decision, rules, providers, prospect)
            out.actions += actions

        elif cls == "negative":
            suppress_email(
                conn,
                RecipientHashes.for_email(ctx.settings.key("suppression_hash_key"), email),
                "objection",
                scope="tenant",
                tenant_id=ctx.tenant_id,
                source_ref=f"reply:{reply_id}:negative",
            )
            if state in (State.REPLIED, State.QUALIFIED):
                transition(
                    conn,
                    lead_id,
                    State.DISQUALIFIED,
                    actor_type="agent",
                    actor_id="reply_handler",
                    reason="not interested",
                )
            out.actions += ["suppressed_for_tenant", "disqualified"]

        elif cls == "not_now":
            body = agent4.draft_reply(
                ctx,
                lead_id,
                intent="not_now",
                first_name=first_name,
                reply_text=msg.body_text,
                language=decision.language,
            )
            if body is None:
                _escalate(conn, ctx.tenant_id, lead_id, "off_playbook", {"why": "draft failed checks"})
                out.actions.append("escalated:draft_failed")
            else:
                _ai_reply(
                    conn,
                    ctx,
                    lead_id=lead_id,
                    reply_id=reply_id,
                    body=body,
                    email=email,
                    subject=i18n.t(
                        cfg.language,
                        "reply_subject",
                        subject=msg.subject or i18n.t(cfg.language, "reply_subject_default"),
                    ),
                    asks_if_ai=rules.asks_if_ai,
                )
                conn.execute(
                    text("UPDATE leads SET paused_until = :p WHERE id = :l"),
                    {"p": at + timedelta(days=cfg.nurture_days), "l": lead_id},
                )
                out.actions += ["reply_drafted_for_approval", f"nurture_until:+{cfg.nurture_days}d"]

        elif cls == "interested":
            out.actions += _interested(
                conn,
                ctx,
                cal,
                lead_id=lead_id,
                reply_id=reply_id,
                msg=msg,
                rules=rules,
                decision=decision,
                offer=offer,
                labels=labels,
                state=state,
                email=email,
                first_name=first_name,
                prospect=prospect,
                now=at,
            )
        return _finish(conn, ctx, out, reply_id, lead_id)


def _finish(conn: Connection, ctx: AgentContext, out: Outcome, reply_id: uuid.UUID, lead_id: uuid.UUID) -> Outcome:
    conn.execute(
        text("UPDATE replies SET handled_at = now(), actions = CAST(:a AS jsonb) WHERE id = :r"),
        {"a": json.dumps(out.actions), "r": reply_id},
    )
    out.lead_state = get_lead(conn, lead_id).state.value
    write_audit(
        conn,
        action=f"reply.handled.{out.classification}",
        actor_type="agent",
        actor_id="reply_handler",
        tenant_id=ctx.tenant_id,
        entity_type="lead",
        entity_id=lead_id,
        after={"actions": out.actions},
    )
    return out


def _interested(
    conn: Connection,
    ctx: AgentContext,
    cal: CalendarGateway,
    *,
    lead_id: uuid.UUID,
    reply_id: uuid.UUID,
    msg: InboundEmail,
    rules: RuleResult,
    decision: HandledReply,
    offer: RowMapping | None,
    labels: list[str],
    state: State,
    email: str,
    first_name: str,
    prospect: RowMapping,
    now: datetime,
) -> list[str]:
    cfg = ctx.config
    llm = decision.llm
    # (a) booked meeting: confirmation or reschedule request
    meeting = conn.execute(
        text("SELECT id FROM meetings WHERE lead_id = :l AND status IN ('booked','confirmed')"), {"l": lead_id}
    ).scalar()
    if meeting:
        if rules.wants_reschedule or (llm and llm.wants_reschedule):
            res = meetings.offer_slots(
                conn,
                ctx.cipher,
                ctx.settings,
                cfg,
                cal,
                tenant_id=ctx.tenant_id,
                lead_id=lead_id,
                reply_id=reply_id,
                now=now,
                intro=i18n.t(cfg.language, "slot_intro_other"),
            )
            return ["reschedule_slots_offered_for_approval" if res else "no_slots_available"]
        if rules.confirms:
            meetings.confirm(conn, ctx.tenant_id, meeting)
            return ["meeting_confirmed"]
        return ["noted_on_booked_meeting"]
    # (b) open slot offer: book the chosen slot
    if offer is not None:
        idx = parse_slot_choice(msg.body_text, labels)
        if idx is None and llm and llm.slot_choice and llm.slot_choice <= len(labels):
            idx = llm.slot_choice - 1
        if idx is not None:
            booked = meetings.book(
                conn,
                ctx.cipher,
                ctx.settings,
                cfg,
                cal,
                tenant_id=ctx.tenant_id,
                lead_id=lead_id,
                offer=offer,
                index=idx,
                now=now,
            )
            if booked["status"] == "calendar_disconnected":
                _escalate(conn, ctx.tenant_id, lead_id, "calendar_disconnected", {"reply_id": str(reply_id)})
                return ["escalated:calendar_disconnected"]
            if booked["status"] == "blocked":
                _escalate(conn, ctx.tenant_id, lead_id, "other", {"booking_blocked": booked.get("reasons")})
                return ["booking_blocked_by_compliance"]
            return [f"booking:{booked['status']}"]
    # (c) playbook objection inside a positive reply
    if llm and llm.playbook_id:
        entry = next(p for p in cfg.objection_playbook if p.id == llm.playbook_id)
        body = agent4.draft_reply(
            ctx,
            lead_id,
            intent="playbook",
            first_name=first_name,
            reply_text=msg.body_text,
            language=decision.language,
            playbook=entry,
        )
        if body is None:
            _escalate(conn, ctx.tenant_id, lead_id, "off_playbook", {"playbook_id": entry.id})
            return ["escalated:off_playbook"]
        _ai_reply(
            conn,
            ctx,
            lead_id=lead_id,
            reply_id=reply_id,
            body=body,
            email=email,
            subject=i18n.t(
                cfg.language, "reply_subject", subject=msg.subject or i18n.t(cfg.language, "reply_subject_default")
            ),
            asks_if_ai=rules.asks_if_ai,
        )
        return [f"playbook_reply_drafted:{entry.id}"]
    # (d) qualification: without signed criteria there is nothing to qualify against, so a person decides
    if cfg.qualification is None or not cfg.qualification.criteria:
        _escalate(conn, ctx.tenant_id, lead_id, "other", {"why": "no qualification criteria configured"})
        return ["escalated:no_qualification_criteria"]
    texts = [
        ctx.cipher.decrypt(conn, ctx.tenant_id, "replies.body", b)
        for b in conn.execute(
            text(
                "SELECT body_text_enc FROM replies WHERE lead_id = :l AND body_text_enc IS NOT NULL "
                "ORDER BY received_at"
            ),
            {"l": lead_id},
        ).scalars()
    ]
    rr = (
        float(prospect["role_relevance"])
        if prospect["role_relevance"] is not None
        else role_relevance(prospect["title"] or "", cfg)
    )
    try:
        overall, evidence, conf = agent4.qualify(ctx, lead_id, texts, rr, prospect["title"])
    except LLMEscalation as exc:  # never guess "met" or "not met" without the model: a human decides
        _escalate(conn, ctx.tenant_id, lead_id, "llm_failure", {"step": "qualify", "reason": exc.reason})
        return ["escalated:llm_failure"]
    if cfg.qualification:
        cfg_id = conn.execute(text("SELECT id FROM tenant_configs ORDER BY version DESC LIMIT 1")).scalar_one()
        conn.execute(
            text("""INSERT INTO qualifications (tenant_id, lead_id, framework, criteria_config_id, evidence,
              overall_met, confidence, decided_by) VALUES (:t, :l, :f, :c, CAST(:e AS jsonb), :m, :cf, 'agent')
              ON CONFLICT (lead_id) DO UPDATE SET evidence = EXCLUDED.evidence, overall_met = EXCLUDED.overall_met,
              confidence = EXCLUDED.confidence, criteria_config_id = EXCLUDED.criteria_config_id,
              updated_at = now()"""),
            {
                "t": ctx.tenant_id,
                "l": lead_id,
                "f": cfg.qualification.framework,
                "c": cfg_id,
                "e": json.dumps(evidence),
                "m": bool(overall),
                "cf": conf,
            },
        )
    if overall is False:
        transition(
            conn,
            lead_id,
            State.DISQUALIFIED,
            actor_type="agent",
            actor_id="reply_handler",
            reason="qualification criteria not met",
        )
        return ["qualification:not_met", "disqualified"]
    if overall is None:
        missing = [
            c.question
            for c in (cfg.qualification.criteria if cfg.qualification else [])
            if evidence.get(c.id, {}).get("met") is not True
        ]
        body = agent4.draft_reply(
            ctx,
            lead_id,
            intent="ask_qualifying",
            first_name=first_name,
            reply_text=msg.body_text,
            language=decision.language,
            missing_criteria=missing,
        )
        if body is None:
            _escalate(conn, ctx.tenant_id, lead_id, "off_playbook", {"why": "qualifying question failed checks"})
            return ["escalated:draft_failed"]
        _ai_reply(
            conn,
            ctx,
            lead_id=lead_id,
            reply_id=reply_id,
            body=body,
            email=email,
            subject=i18n.t(
                cfg.language, "reply_subject", subject=msg.subject or i18n.t(cfg.language, "reply_subject_default")
            ),
            asks_if_ai=rules.asks_if_ai,
        )
        return ["qualification:incomplete", "qualifying_question_drafted_for_approval"]
    if state == State.REPLIED:
        transition(
            conn,
            lead_id,
            State.QUALIFIED,
            actor_type="agent",
            actor_id="reply_handler",
            reason=f"qualified (confidence {conf:.2f})",
        )
    if cfg.calendar.status != "ok":
        _escalate(conn, ctx.tenant_id, lead_id, "calendar_disconnected", {"reply_id": str(reply_id)})
        return ["qualification:met", "escalated:calendar_disconnected"]
    res = meetings.offer_slots(
        conn, ctx.cipher, ctx.settings, cfg, cal, tenant_id=ctx.tenant_id, lead_id=lead_id, reply_id=reply_id, now=now
    )
    return ["qualification:met", "slots_offered_for_approval" if res else "no_slots_available"]


def _referral(
    conn: Connection,
    ctx: AgentContext,
    lead_id: uuid.UUID,
    decision: HandledReply,
    rules: RuleResult,
    providers: Providers | None,
    prospect: RowMapping,
) -> tuple[list[str], uuid.UUID | None]:
    ref = decision.llm.referral if decision.llm and decision.llm.referral else None
    email = (ref.email if ref and ref.email else None) or rules.referral_email
    name = (ref.name if ref and ref.name else None) or rules.referral_name
    if not email:
        transition(
            conn,
            lead_id,
            State.DISQUALIFIED,
            actor_type="agent",
            actor_id="reply_handler",
            reason="referred, but no contact details given",
        )
        _escalate(conn, ctx.tenant_id, lead_id, "other", {"why": "referral without email; a human may follow up"})
        return ["referral:no_email", "escalated"], None
    lead = conn.execute(
        text("SELECT campaign_id, company_id, signal_id FROM leads WHERE id = :l"), {"l": lead_id}
    ).one()
    person = None
    if providers is not None:
        candidates = providers.enrichment.find_people(
            email_domain(email), [t for p in ctx.config.personas for t in p.titles]
        )
        person = next(
            (
                p
                for p in candidates
                if p.professional_email and normalise_email(p.professional_email) == normalise_email(email)
            ),
            None,
        )
    verification = providers.verifier.verify(email) if providers is not None else "unknown"
    legal_form = conn.execute(text("SELECT legal_form FROM companies WHERE id = :c"), {"c": lead.company_id}).scalar()
    sub_type, sub_reason = classify_subscriber(legal_form, email_domain(email), legal_form not in (None, "unknown"))
    loc = parse_location(person.location_text if person else None)
    title = person.title if person else (ref.title if ref else None)
    bidx = blind_index(ctx.settings.key("blind_index_key"), email, ctx.tenant_id)
    pid = conn.execute(text("SELECT id FROM prospects WHERE email_bidx = :b"), {"b": bidx}).scalar()
    if pid is None:
        pid = conn.execute(
            text("""INSERT INTO prospects (tenant_id, company_id, name_enc, title, email_enc, email_bidx,
              country_code, subdivision_code, subscriber_type, subscriber_type_reason, email_verification,
              email_verified_at, role_relevance, confidence_score)
              VALUES (:t, :c, :n, :ti, :e, :b, :cc, :sd, :st, :sr, :v, now(), :rr, :cs)
              RETURNING id"""),
            {
                "t": ctx.tenant_id,
                "c": lead.company_id,
                "n": ctx.cipher.encrypt(
                    conn, ctx.tenant_id, "prospects.name", (person.person_name if person else name) or "Unknown"
                ),
                "ti": title,
                "e": ctx.cipher.encrypt(conn, ctx.tenant_id, "prospects.email", email),
                "b": bidx,
                "cc": loc.country,
                "sd": loc.subdivision,
                "st": sub_type,
                "sr": sub_reason,
                "v": verification,
                "rr": role_relevance(title or "", ctx.config),
                "cs": person.confidence if person else 0.5,
            },
        ).scalar_one()
        src = person.data_source if person else "referral:reply"
        for f in ("name", "email", "title", "location"):
            conn.execute(
                text("""INSERT INTO prospect_field_sources (tenant_id, prospect_id, field_name, data_source)
                  VALUES (:t, :p, :f, :s) ON CONFLICT DO NOTHING"""),
                {"t": ctx.tenant_id, "p": pid, "f": f, "s": src},
            )
    if conn.execute(
        text("SELECT 1 FROM leads WHERE campaign_id = :c AND prospect_id = :p"), {"c": lead.campaign_id, "p": pid}
    ).first():
        transition(conn, lead_id, State.DISQUALIFIED, actor_type="agent", actor_id="reply_handler", reason="referred")
        return ["referral:already_a_lead"], None
    new_id = conn.execute(
        text("""INSERT INTO leads (tenant_id, campaign_id, company_id, prospect_id, signal_id,
          referred_from_lead_id, state) VALUES (:t, :c, :co, :p, :s, :r, 'discovered') RETURNING id"""),
        {"t": ctx.tenant_id, "c": lead.campaign_id, "co": lead.company_id, "p": pid, "s": lead.signal_id, "r": lead_id},
    ).scalar_one()
    transition(conn, new_id, State.ENRICHED, actor_type="agent", actor_id="reply_handler", reason="referral contact")
    if controls.is_held(conn, ctx.tenant_id, lead.campaign_id) is not None:
        # Paused: the referral waits (enriched) and gets its compliance check on the first run after resuming,
        # instead of being rejected now because the client is paused.
        transition(
            conn,
            lead_id,
            State.DISQUALIFIED,
            actor_type="agent",
            actor_id="reply_handler",
            reason="referred to a colleague",
        )
        return ["referral:new_lead:held_while_paused", "original_lead_closed"], None
    _, d = run_gate(
        conn,
        tenant_id=ctx.tenant_id,
        lead_id=new_id,
        cipher=ctx.cipher,
        suppression_key=ctx.settings.key("suppression_hash_key"),
        stage="referral",
    )
    transition(
        conn,
        lead_id,
        State.DISQUALIFIED,
        actor_type="agent",
        actor_id="reply_handler",
        reason="referred to a colleague",
    )
    conn.execute(
        text("INSERT INTO outbox (tenant_id, topic, payload) VALUES (:t, 'lead.referral_checked', CAST(:p AS jsonb))"),
        {"t": ctx.tenant_id, "p": json.dumps({"lead_id": str(new_id)})},
    )
    return [f"referral:new_lead:{d.outcome}", "original_lead_closed"], new_id
