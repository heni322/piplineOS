"""Phase 3 vertical slice orchestration:
Agent 1 -> Agent 2 -> compliance gate -> Agent 3 -> human approval queue -> dry-run send (outbound gateway).
Each step is idempotent (state-checked), so retries and duplicate events are harmless."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.companies_house.base import CompaniesRegistry
from app.adapters.enrichment.base import EnrichmentProvider
from app.adapters.inbound.base import InboundEmail, Inbox
from app.adapters.inbound.mock import MockInbox
from app.adapters.verifier.base import EmailVerifier
from app.agents.base import AgentContext
from app.agents.decision_maker.agent import find_decision_maker
from app.agents.trigger_hunter.agent import HuntStats, SourceBinding, hunt
from app.agents.writer.agent import write_for_lead
from app.core.crypto import PiiCipher
from app.core.logging import get_logger
from app.db.session import DbContext, transaction
from app.domain.approvals import service as approvals
from app.domain.compliance.gate import run_gate
from app.domain.deliverability.hours import add_business_days
from app.domain.meetings import service as meetings
from app.domain.outbound.calendar import CalendarGateway
from app.domain.outbound.gateway import OutboundGateway, SendOutcome
from app.domain.outbound.providers import email_senders
from app.domain.replies import service as replies
from app.domain.tenants.config import load_config
from app.llm.client import LLMBackend
from app.llm.factory import build_llm_client
from app.settings import Settings

log = get_logger(__name__)


@dataclass
class Providers:
    sources: list[SourceBinding]
    enrichment: EnrichmentProvider
    verifier: EmailVerifier
    registry: CompaniesRegistry
    inbox: Inbox = field(default_factory=MockInbox)


@dataclass
class CampaignRun:
    hunt: HuntStats
    outcomes: dict[str, int] = field(default_factory=dict)


def agent_context(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    llm_backend: LLMBackend | None = None,
) -> AgentContext:
    with transaction(DbContext.system(tenant_id), engine) as conn:
        config_id, cfg = load_config(conn, tenant_id)
        live = bool(conn.execute(text("SELECT live_mode FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar())
        golive = bool(conn.execute(text("SELECT golive_complete('uk')")).scalar())
    llm = build_llm_client(settings, engine, cipher, tenant_live=live, golive_complete=golive, backend=llm_backend)
    return AgentContext(settings, engine, cipher, llm, tenant_id, campaign_id, cfg, config_id)


def process_lead(ctx: AgentContext, lead_id: uuid.UUID, providers: Providers) -> str:
    find_decision_maker(ctx, lead_id, providers.enrichment, providers.verifier, providers.registry)
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        state = conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead_id}).scalar_one()
        if state == "enriched":
            run_gate(
                conn,
                tenant_id=ctx.tenant_id,
                lead_id=lead_id,
                cipher=ctx.cipher,
                suppression_key=ctx.settings.key("suppression_hash_key"),
            )
            state = conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead_id}).scalar_one()
    if state == "compliance_checked":
        result = write_for_lead(ctx, lead_id)
        state = result.status if result.status != "skipped" else state
        if result.status == "pending_approval":
            with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
                if approvals.route(conn, ctx.tenant_id, lead_id) == "auto":
                    state = "approved"
    return str(state)


def run_campaign(ctx: AgentContext, providers: Providers, since: datetime | None = None) -> CampaignRun:
    stats = hunt(ctx, providers.sources, since or datetime.now(UTC) - timedelta(days=30))
    run = CampaignRun(stats)
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        lead_ids = (
            conn.execute(
                text("SELECT id FROM leads WHERE campaign_id = :c AND state = 'discovered' ORDER BY created_at"),
                {"c": ctx.campaign_id},
            )
            .scalars()
            .all()
        )
    for lid in lead_ids:
        final = process_lead(ctx, lid, providers)
        run.outcomes[final] = run.outcomes.get(final, 0) + 1
    log.info("campaign_run", tenant_id=str(ctx.tenant_id), discovered=stats.discovered, outcomes=run.outcomes)
    return run


def queue_due_reminders(
    engine: Engine, settings: Settings, cipher: PiiCipher, tenant_id: uuid.UUID, now: datetime
) -> int:
    """Turns due meeting reminders into templated messages (then sent by send_due through the gateway)."""
    n = 0
    with transaction(DbContext.system(tenant_id), engine) as conn:
        _, cfg = load_config(conn, tenant_id)
        conn.execute(
            text("""UPDATE meeting_reminders r SET status = 'cancelled' FROM meetings m WHERE m.id = r.meeting_id
              AND r.status = 'scheduled' AND m.status NOT IN ('booked','confirmed')""")
        )
        due = conn.execute(
            text("""SELECT r.id, r.meeting_id, r.offset_minutes, m.lead_id, m.starts_at, m.ends_at,
              l.objected_after_booking FROM meeting_reminders r JOIN meetings m ON m.id = r.meeting_id
              JOIN leads l ON l.id = m.lead_id WHERE r.status = 'scheduled' AND r.message_id IS NULL
              AND r.scheduled_for <= :now AND m.status IN ('booked','confirmed') FOR UPDATE OF r"""),
            {"now": now},
        ).all()
        for r in due:
            if r.objected_after_booking and not cfg.show_rate.reminders_after_objection:
                conn.execute(text("UPDATE meeting_reminders SET status = 'skipped' WHERE id = :r"), {"r": r.id})
                continue
            p = meetings.load_prospect(conn, cipher, tenant_id, r.lead_id)
            when = meetings.label(meetings.Slot(r.starts_at, r.ends_at), p.tz)
            soon = "tomorrow" if r.offset_minutes >= 720 else "shortly"
            body = (
                f"Hi {p.first_name},\n\nA quick reminder of our call {soon}: {when}.\n\nAgenda:\n"
                f"{cfg.show_rate.agenda}\n\nIf the time no longer works, just reply and I will find another.\n\n"
                f"{cfg.brand_voice.sender_name.split()[0]}"
            )
            mid = meetings.template_message(
                conn,
                cipher,
                settings,
                cfg,
                tenant_id=tenant_id,
                lead_id=r.lead_id,
                kind="meeting_reminder",
                body=body,
                status="approved",
                subject=f"Reminder: {cfg.show_rate.meeting_title}, {when}",
                key=f"reminder:{r.id}",
                meeting_id=r.meeting_id,
            )
            conn.execute(text("UPDATE meeting_reminders SET message_id = :m WHERE id = :r"), {"m": mid, "r": r.id})
            n += 1
    return n


def send_due(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    tenant_id: uuid.UUID,
    now: datetime | None = None,
    gateway: OutboundGateway | None = None,
) -> list[SendOutcome]:
    """Send approved messages that are due (dry-run unless all live keys are on). Schedules follow-ups."""
    at = now or datetime.now(UTC)
    queue_due_reminders(engine, settings, cipher, tenant_id, at)
    if gateway is None:
        live, sandbox = email_senders(settings)
        gateway = OutboundGateway(settings, engine, cipher, live_sender=live, sandbox_sender=sandbox, clock=lambda: at)
    gw = gateway
    with transaction(DbContext.system(tenant_id), engine) as conn:
        due = (
            conn.execute(
                text("""SELECT m.id FROM messages m WHERE m.status IN ('approved','queued','failed_retryable')
              AND (m.scheduled_for IS NULL OR m.scheduled_for <= :now)
              AND (m.sequence_step = 0 OR m.scheduled_for IS NOT NULL)
              ORDER BY m.sequence_step, m.created_at LIMIT 500"""),
                {"now": at},
            )
            .scalars()
            .all()
        )
    results = []
    for mid in due:
        out = gw.send_message(tenant_id, mid)
        results.append(out)
        if out.status in ("sent", "dry_run_sent"):
            _schedule_follow_ups(engine, settings, tenant_id, mid, at)
            with transaction(DbContext.system(tenant_id), engine) as conn:
                conn.execute(
                    text("UPDATE meeting_reminders SET status = :s WHERE message_id = :m"), {"s": out.status, "m": mid}
                )
    return results


def process_inbound(
    ctx: AgentContext,
    messages: list[InboundEmail],
    providers: Providers,
    calendar: CalendarGateway | None = None,
    now: datetime | None = None,
) -> list[replies.Outcome]:
    """Agent 4 over new inbound replies. Referral leads are drafted after their compliance check commits;
    AI-handled replies follow the tenant's approval mode."""
    cal = calendar or CalendarGateway(ctx.settings, ctx.cipher)
    outcomes = []
    for msg in messages:
        out = replies.handle(ctx, msg, providers=providers, calendar=cal, now=now)
        outcomes.append(out)
        if out.new_lead_id is not None:
            with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
                state = conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": out.new_lead_id}).scalar_one()
            if state == "compliance_checked" and write_for_lead(ctx, out.new_lead_id).status == "pending_approval":
                with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
                    approvals.route(conn, ctx.tenant_id, out.new_lead_id)
        if out.reply_id is not None:
            with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
                for mid, lid in conn.execute(
                    text(
                        "SELECT id, lead_id FROM messages WHERE reply_id = :r AND kind = "
                        "'agent_reply' AND status = 'pending_approval'"
                    ),
                    {"r": out.reply_id},
                ):
                    approvals.route_reply(conn, ctx.tenant_id, mid, lid)
                for mid, lid in conn.execute(
                    text(
                        "SELECT m.id, m.lead_id FROM messages m JOIN slot_offers o ON o.message_id "
                        "= m.id WHERE m.status = 'pending_approval' AND o.status = 'open' AND "
                        "m.reply_id IS NULL AND m.lead_id IN (SELECT lead_id FROM replies WHERE "
                        "id = :r)"
                    ),
                    {"r": out.reply_id},
                ):
                    approvals.route_reply(conn, ctx.tenant_id, mid, lid)
    return outcomes


def reply_sla_breaches(engine: Engine, tenant_id: uuid.UUID, minutes: int = 5, now: datetime | None = None) -> int:
    """Replies received more than `minutes` ago that are still unclassified (target: classified within 5 minutes)."""
    with transaction(DbContext.system(tenant_id), engine) as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM replies WHERE classified_at IS NULL AND received_at < :t"),
                {"t": (now or datetime.now(UTC)) - timedelta(minutes=minutes)},
            ).scalar_one()
        )


def _schedule_follow_ups(
    engine: Engine, settings: Settings, tenant_id: uuid.UUID, message_id: uuid.UUID, sent_at: datetime
) -> None:
    with transaction(DbContext.system(tenant_id), engine) as conn:
        head = (
            conn.execute(
                text("""SELECT m.lead_id, m.sequence_step, p.country_code, p.subdivision_code
              FROM messages m JOIN leads l ON l.id = m.lead_id JOIN prospects p ON p.id = l.prospect_id
              WHERE m.id = :m"""),
                {"m": message_id},
            )
            .mappings()
            .one()
        )
        if head["sequence_step"] != 0:
            return
        _, cfg = load_config(conn, tenant_id)
        steps = conn.execute(
            text(
                "SELECT id, sequence_step FROM messages WHERE lead_id = :l AND sequence_step > 0 "
                "AND status = 'approved' AND scheduled_for IS NULL"
            ),
            {"l": head["lead_id"]},
        ).all()
        for s in steps:
            when = add_business_days(
                sent_at,
                cfg.sequence.spacing_business_days * s.sequence_step,
                head["country_code"],
                head["subdivision_code"],
            )
            conn.execute(text("UPDATE messages SET scheduled_for = :w WHERE id = :m"), {"w": when, "m": s.id})
