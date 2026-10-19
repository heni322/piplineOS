"""Celery application: queues default / send / replies; beat schedule for system jobs (ADR-0001)."""

from __future__ import annotations

import uuid
from typing import Any

from celery import Celery
from sqlalchemy import text

from app.core.logging import configure_logging, get_logger
from app.db.session import DbContext, get_engine, transaction
from app.domain.outbound.gateway import reconcile_stuck_sends
from app.domain.tenants.controls import LISTENING_STATUSES
from app.settings import get_settings
from app.workflows.outbox import relay_batch

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger(__name__)

celery_app = Celery("pipelineos", broker=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={"pipelineos.send.*": {"queue": "send"}, "pipelineos.replies.*": {"queue": "replies"}},
    broker_transport_options={"visibility_timeout": 3600},
    timezone="UTC",
    beat_schedule={
        "outbox-relay": {"task": "pipelineos.system.relay_outbox", "schedule": 5.0},
        "reconcile-sends": {"task": "pipelineos.system.reconcile_sends", "schedule": 60.0},
        "send-due": {"task": "pipelineos.send.send_due", "schedule": 300.0},
        "fetch-inbound": {"task": "pipelineos.replies.fetch_inbound", "schedule": 60.0},
        "sweep-inbound": {"task": "pipelineos.replies.sweep_inbound", "schedule": 900.0},
        "reply-sla": {"task": "pipelineos.system.reply_sla", "schedule": 60.0},
        "deliverability-kill-switch": {"task": "pipelineos.system.deliverability", "schedule": 900.0},
        "crm-sync": {"task": "pipelineos.system.crm_sync", "schedule": 300.0},
        "daily-deliverability": {"task": "pipelineos.system.daily_deliverability", "schedule": 24 * 3600.0},
        "daily-retention": {"task": "pipelineos.system.retention", "schedule": 24 * 3600.0},
        "weekly-client-report": {"task": "pipelineos.system.weekly_report", "schedule": 6 * 3600.0},
        "renew-graph-subscriptions": {"task": "pipelineos.replies.renew_graph_subscriptions", "schedule": 6 * 3600.0},
        "renew-gmail-watches": {"task": "pipelineos.replies.renew_gmail_watches", "schedule": 24 * 3600.0},
        **(
            {
                "ingest-filings": {
                    "task": "pipelineos.signals.ingest_filings",
                    "schedule": settings.filing_stream_interval_minutes * 60.0,
                }
            }
            if settings.filing_stream_interval_minutes > 0 and settings.companies_house_stream_key is not None
            else {}
        ),
        **(
            {"hunt": {"task": "pipelineos.pipeline.hunt", "schedule": settings.hunt_interval_hours * 3600.0}}
            if settings.hunt_interval_hours > 0
            else {}
        ),
    },
)


def _dispatch(topic: str, payload: dict[str, Any]) -> None:
    log.info("outbox_event", topic=topic, lead_id=payload.get("lead_id"))
    if topic == "lead.discovered":
        process_lead_task.delay(payload["lead_id"])
    elif topic == "lead.referral_checked":
        draft_referral_task.delay(payload["lead_id"])


@celery_app.task(
    name="pipelineos.pipeline.process_lead", autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=5
)
def process_lead_task(lead_id: str) -> str:
    """Agent 2 -> compliance gate -> Agent 3 -> approval routing. Idempotent: state-checked at every step."""
    from app.adapters.registry import providers_for
    from app.core.crypto import PiiCipher
    from app.workflows.pipeline import agent_context, process_lead

    engine = get_engine()
    with transaction(DbContext(role="operator"), engine) as conn:  # resolve tenant/campaign of the lead
        row = conn.execute(text("SELECT tenant_id, campaign_id FROM leads WHERE id = :l"), {"l": lead_id}).one()
    ctx = agent_context(engine, settings, PiiCipher(settings.key("pii_master_key")), row.tenant_id, row.campaign_id)
    return process_lead(ctx, uuid.UUID(lead_id), providers_for(settings, ctx))


@celery_app.task(name="pipelineos.pipeline.hunt")
def hunt_task() -> dict[str, int]:
    """Agent 1 for every active campaign, then Agents 2-3 for waiting leads (paid lookups via the three-key gate)."""
    from app.core.crypto import PiiCipher
    from app.workflows.pipeline import hunt_active_campaigns

    return hunt_active_campaigns(get_engine(), settings, PiiCipher(settings.key("pii_master_key")))


@celery_app.task(name="pipelineos.places.company_search", time_limit=900, soft_time_limit=840)
def company_search_task(search_id: str, tenant_id: str) -> str:
    """A "Find companies" search from the console (app.domain.paid.searches): paid, gated and capped."""
    from app.core.crypto import PiiCipher
    from app.domain.paid.searches import run

    cipher = PiiCipher(settings.key("pii_master_key"))
    return run(get_engine(), settings, cipher, uuid.UUID(search_id), uuid.UUID(tenant_id))


@celery_app.task(name="pipelineos.signals.ingest_filings")
def ingest_filings_task() -> dict[str, object]:
    """Market-wide funding triggers from the Companies House filing stream (app.workflows.register_ingest)."""
    from app.adapters.companies_house.live import CompaniesHouseRegistry
    from app.adapters.companies_house.stream import FilingStream
    from app.workflows.register_ingest import ingest_filings

    if settings.companies_house_stream_key is None or settings.companies_house_api_key is None:
        return {"skipped": "COMPANIES_HOUSE_STREAM_KEY and COMPANIES_HOUSE_API_KEY are both needed"}
    stats = ingest_filings(
        get_engine(),
        FilingStream(settings.companies_house_stream_key.get_secret_value()),
        CompaniesHouseRegistry(settings.companies_house_api_key.get_secret_value()),
        max_seconds=min(240.0, settings.filing_stream_interval_minutes * 60.0 * 0.8),
    )
    return dict(stats.__dict__)


@celery_app.task(name="pipelineos.system.daily_deliverability")
def daily_deliverability_task() -> dict[str, int]:
    """Once a day: re-check every sending domain's DNS, and advance mailbox warm-up for yesterday."""
    from datetime import UTC, datetime

    from app.workflows.daily import daily_deliverability

    return daily_deliverability(get_engine(), now=datetime.now(UTC))


@celery_app.task(name="pipelineos.system.weekly_report")
def weekly_report_task() -> int:
    """Each client's admins get last week's results on Monday (once per person per week; dry-run until live)."""
    from app.core.crypto import PiiCipher
    from app.domain.reporting.weekly import weekly_digest

    return weekly_digest(get_engine(), settings, PiiCipher(settings.key("pii_master_key")))


@celery_app.task(name="pipelineos.system.retention")
def retention_task() -> dict[str, int]:
    """Once a day: erase prospects past the retention periods (config/retention.yaml). Does nothing until the
    solicitor's periods are set there."""
    from datetime import UTC, datetime

    from app.config_paths import config_root
    from app.domain.dpr.retention import RetentionPolicy, run_retention

    policy = RetentionPolicy.load(config_root())
    if not policy.enabled:
        return {"enabled": 0, "erased": 0}
    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:
        tenant_ids = conn.execute(text("SELECT id FROM tenants")).scalars().all()
    erased = 0
    now = datetime.now(UTC)
    for t in tenant_ids:
        with transaction(DbContext.system(t), engine) as conn:
            erased += run_retention(conn, t, policy, now, apply=True).erased
    return {"enabled": 1, "erased": erased}


@celery_app.task(name="pipelineos.system.crm_sync")
def crm_sync_task() -> int:
    """Clients' CRMs: write booked meetings (dry-run unless all three keys are on), read back attendance."""
    from datetime import UTC, datetime

    from app.core.crypto import PiiCipher
    from app.domain.outbound.crm import sync_tenant

    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:
        tenant_ids = conn.execute(text("SELECT id FROM tenants WHERE status = 'active'")).scalars().all()
    cipher = PiiCipher(settings.key("pii_master_key"))
    now = datetime.now(UTC)
    done = 0
    for t in tenant_ids:
        r = sync_tenant(engine, settings, cipher, t, now=now)
        done += r.recorded + r.written + r.attendance
    return done


@celery_app.task(name="pipelineos.send.send_due")
def send_due_task() -> int:
    from app.core.crypto import PiiCipher
    from app.workflows.pipeline import send_due

    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:
        tenant_ids = conn.execute(text("SELECT id FROM tenants WHERE status = 'active'")).scalars().all()
    cipher = PiiCipher(settings.key("pii_master_key"))
    return sum(len(send_due(engine, settings, cipher, t)) for t in tenant_ids)


@celery_app.task(name="pipelineos.system.relay_outbox")
def relay_outbox() -> int:
    return relay_batch(get_engine(), _dispatch)


@celery_app.task(name="pipelineos.system.reconcile_sends")
def reconcile_sends() -> int:
    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:
        tenant_ids = conn.execute(text("SELECT id FROM tenants WHERE status <> 'offboarded'")).scalars().all()
    return sum(reconcile_stuck_sends(engine, t, settings.sending_reconcile_after_minutes) for t in tenant_ids)


def _ctx_for_lead(lead_id: str):  # type: ignore[no-untyped-def]
    from app.core.crypto import PiiCipher
    from app.workflows.pipeline import agent_context

    engine = get_engine()
    with transaction(DbContext(role="operator"), engine) as conn:
        row = conn.execute(text("SELECT tenant_id, campaign_id FROM leads WHERE id = :l"), {"l": lead_id}).one()
    return agent_context(engine, settings, PiiCipher(settings.key("pii_master_key")), row.tenant_id, row.campaign_id)


@celery_app.task(name="pipelineos.pipeline.draft_referral", autoretry_for=(ConnectionError,), retry_backoff=True)
def draft_referral_task(lead_id: str) -> str:
    """Recovery path for referral leads: drafts only if compliance allowed it and no draft exists yet (the inbound
    handler normally drafts straight after its commit, so this is usually a no-op)."""
    from app.agents.writer.agent import write_for_lead
    from app.domain.approvals import service as approvals

    ctx = _ctx_for_lead(lead_id)
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        state = conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead_id}).scalar_one()
        drafted = conn.execute(
            text("SELECT 1 FROM messages WHERE lead_id = :l AND kind = 'outreach' LIMIT 1"), {"l": lead_id}
        ).first()
    if state != "compliance_checked" or drafted:
        return "skipped"
    if write_for_lead(ctx, uuid.UUID(lead_id)).status == "pending_approval":
        with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
            approvals.route(conn, ctx.tenant_id, uuid.UUID(lead_id))
    return "drafted"


@celery_app.task(name="pipelineos.replies.fetch_inbound", autoretry_for=(ConnectionError,), retry_backoff=True)
def fetch_inbound_task() -> int:
    """Pulls new replies per tenant mailbox and runs Agent 4 (mock mailboxes; real ones push and are swept)."""
    from app.adapters.registry import inbox_for, providers_for
    from app.core.crypto import PiiCipher
    from app.workflows.pipeline import agent_context, process_inbound

    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:
        rows = conn.execute(
            text("""SELECT t.id, (SELECT c.id FROM campaigns c WHERE c.tenant_id = t.id ORDER BY c.created_at
              LIMIT 1) AS campaign_id FROM tenants t WHERE t.status = ANY(:s)"""),
            {"s": list(LISTENING_STATUSES)},
        ).all()
    cipher, handled = PiiCipher(settings.key("pii_master_key")), 0
    for tenant_id, campaign_id in rows:
        msgs = inbox_for(settings, tenant_id).fetch_new()
        if not msgs or campaign_id is None:
            continue
        ctx = agent_context(engine, settings, cipher, tenant_id, campaign_id)
        handled += len(process_inbound(ctx, msgs, providers_for(settings, ctx)))
    return handled


@celery_app.task(name="pipelineos.replies.sweep_inbound")
def sweep_inbound_task() -> dict[str, object]:
    """Safety net: replies a push notification missed (see app.workflows.inbound_sweep)."""
    from app.core.crypto import PiiCipher
    from app.workflows.inbound_sweep import sweep_inboxes

    return sweep_inboxes(get_engine(), settings, PiiCipher(settings.key("pii_master_key")))


@celery_app.task(name="pipelineos.system.reply_sla")
def reply_sla_task() -> int:
    """Target: every reply classified within 5 minutes. Breaches are logged for alerting (no PII in the log)."""
    from app.workflows.pipeline import reply_sla_breaches

    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:  # paused clients' replies are still handled
        tenant_ids = (
            conn.execute(text("SELECT id FROM tenants WHERE status = ANY(:s)"), {"s": list(LISTENING_STATUSES)})
            .scalars()
            .all()
        )
    total = 0
    for t in tenant_ids:
        n = reply_sla_breaches(engine, t)
        if n:
            log.warning("reply_sla_breach", tenant_id=str(t), count=n)
        total += n
    return total


@celery_app.task(
    name="pipelineos.replies.graph_message", autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=8
)
def graph_message_task(mailbox: str, message_id: str) -> str:
    """Fetches one inbound message from Microsoft Graph and runs Agent 4 on it (idempotent by message id)."""
    from app.adapters.inbound.msgraph import GraphInbox
    from app.adapters.registry import providers_for
    from app.core.crypto import PiiCipher
    from app.domain.outbound.providers import calendar_gateway, graph_credentials
    from app.workflows.pipeline import agent_context, process_inbound

    engine = get_engine()
    with transaction(DbContext(role="operator"), engine) as conn:
        row = conn.execute(
            text("""SELECT i.tenant_id, (SELECT c.id FROM campaigns c WHERE c.tenant_id = i.tenant_id
              ORDER BY c.created_at LIMIT 1) AS campaign_id FROM inboxes i WHERE lower(i.address) = lower(:a)"""),
            {"a": mailbox},
        ).first()
    if row is None or row.campaign_id is None:
        log.warning("graph_message_unknown_mailbox")
        return "ignored"
    msg = GraphInbox(graph_credentials(settings), mailbox).fetch_message(message_id)
    cipher = PiiCipher(settings.key("pii_master_key"))
    ctx = agent_context(engine, settings, cipher, row.tenant_id, row.campaign_id)
    out = process_inbound(
        ctx, [msg], providers_for(settings, ctx), calendar_gateway(settings, cipher, mailbox, "msgraph")
    )[0]
    return out.classification


@celery_app.task(name="pipelineos.replies.renew_graph_subscriptions")
def renew_graph_subscriptions_task() -> int:
    if settings.email_adapter_mode == "mock" or not settings.msgraph_client_id:
        return 0
    from app.adapters.inbound.msgraph import renew_all
    from app.domain.outbound.providers import graph_credentials

    return renew_all(graph_credentials(settings))


@celery_app.task(
    name="pipelineos.replies.gmail_history", autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=8
)
def gmail_history_task(mailbox: str, history_id: int) -> int:
    """Fetches what arrived in a Google Workspace inbox since the last processed historyId and runs Agent 4 on each
    reply (idempotent by Message-ID). The cursor only moves forward."""
    from app.adapters.inbound.base import InboundEmail
    from app.adapters.inbound.gmail import GmailInbox
    from app.adapters.registry import providers_for
    from app.core.crypto import PiiCipher
    from app.domain.outbound.providers import calendar_gateway, google_credentials
    from app.workflows.pipeline import agent_context, process_inbound

    engine = get_engine()
    with transaction(DbContext(role="operator"), engine) as conn:
        row = conn.execute(
            text("""SELECT i.id, i.tenant_id, i.history_id,
                (SELECT c.id FROM campaigns c WHERE c.tenant_id = i.tenant_id
                 ORDER BY c.created_at LIMIT 1) AS campaign_id
              FROM inboxes i
              WHERE i.provider = 'google' AND lower(i.address) = lower(:a)"""),
            {"a": mailbox},
        ).first()
    if row is None or row.campaign_id is None:
        log.warning("gmail_unknown_mailbox")
        return 0
    msgs: list[InboundEmail] = []
    newest = history_id
    if row.history_id is None:  # first notification after the watch started: nothing older is processed
        pass
    else:
        msgs, newest = GmailInbox(google_credentials(settings), mailbox).fetch_since(int(row.history_id))
    if msgs:
        cipher = PiiCipher(settings.key("pii_master_key"))
        ctx = agent_context(engine, settings, cipher, row.tenant_id, row.campaign_id)
        process_inbound(ctx, msgs, providers_for(settings, ctx), calendar_gateway(settings, cipher, mailbox, "google"))
    with transaction(DbContext(role="operator"), engine) as conn:
        conn.execute(
            text("UPDATE inboxes SET history_id = greatest(coalesce(history_id, 0), :h) WHERE id = :i"),
            {"h": max(newest, history_id), "i": row.id},
        )
    return len(msgs)


@celery_app.task(name="pipelineos.replies.renew_gmail_watches")
def renew_gmail_watches_task() -> int:
    """Gmail watches expire after 7 days; renewing daily keeps push notifications flowing."""
    if settings.email_adapter_mode == "mock" or not (
        settings.google_service_account_json and settings.google_pubsub_topic
    ):
        return 0
    from app.adapters.inbound.gmail import watch
    from app.domain.outbound.providers import google_credentials

    creds = google_credentials(settings)
    with transaction(DbContext(role="operator"), get_engine()) as conn:
        boxes = list(conn.execute(text("SELECT address FROM inboxes WHERE provider = 'google'")).scalars())
    for mb in boxes:
        watch(creds, mb, settings.google_pubsub_topic)
    return len(boxes)


@celery_app.task(name="pipelineos.system.deliverability")
def deliverability_task() -> int:
    """Pauses any inbox whose live bounce/complaint rate crosses the limits in config/deliverability.yaml."""
    from datetime import UTC, datetime

    from app.config_paths import config_root
    from app.domain.deliverability.killswitch import Thresholds, evaluate

    th = Thresholds.load(config_root())
    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:  # bounces and complaints still count during a pause
        tenant_ids = (
            conn.execute(text("SELECT id FROM tenants WHERE status = ANY(:s)"), {"s": list(LISTENING_STATUSES)})
            .scalars()
            .all()
        )
    paused = 0
    for t in tenant_ids:
        with transaction(DbContext.system(t), engine) as conn:
            paused += sum(1 for r in evaluate(conn, t, datetime.now(UTC), th) if r["paused"])
    if paused:
        log.warning("inboxes_paused", count=paused)
    return paused
