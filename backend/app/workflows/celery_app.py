"""Celery application: queues default / send / replies; beat schedule for system jobs (ADR-0001)."""

from __future__ import annotations

import uuid
from typing import Any

from celery import Celery
from sqlalchemy import text

from app.core.logging import configure_logging, get_logger
from app.db.session import DbContext, get_engine, transaction
from app.domain.outbound.gateway import reconcile_stuck_sends
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
    },
)


def _dispatch(topic: str, payload: dict[str, Any]) -> None:
    log.info("outbox_event", topic=topic, lead_id=payload.get("lead_id"))
    if topic == "lead.discovered":
        process_lead_task.delay(payload["lead_id"])


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
    return process_lead(ctx, uuid.UUID(lead_id), providers_for(settings))


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
