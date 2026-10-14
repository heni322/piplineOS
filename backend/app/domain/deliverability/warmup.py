"""Mailbox warm-up. A new inbox starts "warming" with a small daily cap; for every day it actually sent LIVE email,
the cap rises (config/warmup.yaml) until it reaches the target, then the inbox becomes "active" if its bounce and
complaint rates are healthy (the kill switch's thresholds). Dry-run days don't count, because they warm nothing.
A paused inbox never advances."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit


class WarmupPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_cap: int = Field(default=10, ge=1, le=50)
    daily_increase: int = Field(default=2, ge=0, le=20)
    target_cap: int = Field(default=40, ge=1, le=50)
    days_to_active: int = Field(default=21, ge=1, le=90)

    @classmethod
    def load(cls, root: Path) -> WarmupPlan:
        path = root / "warmup.yaml"
        return cls.model_validate(yaml.safe_load(path.read_text())) if path.exists() else cls()

    def cap_for(self, warm_days: int) -> int:
        return min(self.target_cap, self.start_cap + self.daily_increase * warm_days)


@dataclass(frozen=True)
class WarmupStep:
    inbox_id: uuid.UUID
    warmup_day: int
    daily_cap: int
    promoted: bool


def advance(conn: Connection, plan: WarmupPlan, *, day: date, healthy: dict[uuid.UUID, bool]) -> list[WarmupStep]:
    """Run once a day per tenant (tenant context). `day` = the day just finished; `healthy` = kill-switch view per
    inbox (False -> no promotion). Idempotent per inbox per day."""
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    rows = conn.execute(
        text("""SELECT i.id, i.tenant_id, i.warmup_day, i.daily_cap, i.warmup_last_day,
                  (SELECT count(*) FROM messages m WHERE m.inbox_id = i.id AND m.mode_at_send = 'live'
                     AND m.sent_at >= :s AND m.sent_at < :e) AS live_sent
                FROM inboxes i WHERE i.status = 'warming'"""),
        {"s": start, "e": end},
    ).all()
    steps = []
    for r in rows:
        if r.warmup_last_day is not None and r.warmup_last_day >= day:
            continue  # already advanced for this day
        if r.live_sent == 0:
            # nothing live went out: the cap is (re)set to the plan for the days warmed so far, no step
            cap = plan.cap_for(r.warmup_day)
            if cap != r.daily_cap:
                conn.execute(text("UPDATE inboxes SET daily_cap = :c WHERE id = :i"), {"c": cap, "i": r.id})
            continue
        warm = r.warmup_day + 1
        cap = plan.cap_for(warm)
        promote = warm >= plan.days_to_active and cap >= plan.target_cap and healthy.get(r.id, False)
        conn.execute(
            text("""UPDATE inboxes SET warmup_day = :w, daily_cap = :c, warmup_last_day = :d,
                      status = CASE WHEN :p THEN 'active' ELSE status END WHERE id = :i"""),
            {"w": warm, "c": cap, "d": day, "p": promote, "i": r.id},
        )
        if promote:
            write_audit(
                conn,
                action="inbox.warmup_complete",
                actor_type="system",
                actor_id="warmup",
                tenant_id=r.tenant_id,
                entity_type="inbox",
                entity_id=r.id,
                after={"warmup_day": warm, "daily_cap": cap},
            )
        steps.append(WarmupStep(r.id, warm, cap, promote))
    return steps
