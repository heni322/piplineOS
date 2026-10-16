"""Daily deliverability housekeeping: sending-domain DNS re-check, and mailbox warm-up for the day just ended."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config_paths import config_root
from app.core.logging import get_logger
from app.db.session import DbContext, transaction
from app.domain.deliverability import killswitch, warmup
from app.domain.deliverability.dns_check import Lookup, check_and_store, dns_lookup

log = get_logger(__name__)


def daily_deliverability(engine: Engine, *, now: datetime, lookup: Lookup | None = None) -> dict[str, int]:
    # strict: a resolver outage raises (the domain is skipped and keeps yesterday's result) instead of looking
    # like "no SPF/DKIM/DMARC", which would clear the domain's verification.
    look = lookup or dns_lookup(strict=True)
    plan = warmup.WarmupPlan.load(config_root())
    th = killswitch.Thresholds.load(config_root())
    totals = {"domains_checked": 0, "domains_ok": 0, "inboxes_advanced": 0, "inboxes_promoted": 0}
    with transaction(DbContext.system(), engine) as conn:
        tenant_ids = conn.execute(text("SELECT id FROM tenants WHERE status = 'active'")).scalars().all()
    for t in tenant_ids:
        with transaction(DbContext.system(t), engine) as conn:
            domain_ids: list[uuid.UUID] = list(conn.execute(text("SELECT id FROM sending_domains")).scalars())
        for d in domain_ids:
            try:
                with transaction(DbContext.system(t), engine) as conn:
                    res = check_and_store(conn, d, look, now)
            except Exception as exc:  # a DNS outage must not stop the rest of the run
                log.warning("dns_check_failed", tenant_id=str(t), error=type(exc).__name__)
                continue
            totals["domains_checked"] += 1
            totals["domains_ok"] += int(res.ok)
        with transaction(DbContext.system(t), engine) as conn:
            health = killswitch.evaluate(conn, t, now, th)
            healthy = {uuid.UUID(str(h["inbox_id"])): not h["paused"] and not h["warn"] for h in health}
            steps = warmup.advance(conn, plan, day=(now - timedelta(days=1)).date(), healthy=healthy)
        totals["inboxes_advanced"] += len(steps)
        totals["inboxes_promoted"] += sum(s.promoted for s in steps)
    log.info("daily_deliverability", **totals)
    return totals
