"""Retention schedule: erase prospects whose data is past the periods in config/retention.yaml.

Off until the solicitor sets the periods (null = never). Uses the same anonymisation as an erasure request, per
prospect, only when every lead for that person is past its period and nobody ever replied. Dry-run by default:
`apply=False` only counts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit
from app.domain.dpr.service import anonymise_prospect

# Lead states that may expire: nobody in them ever replied, booked or met.
EXPIRABLE = (
    "discovered",
    "enriched",
    "compliance_checked",
    "rejected_by_compliance",
    "alternative_channel",
    "disqualified",
    "contacted",
    "suppressed",
    "objected",
)
SENT = ("sent", "dry_run_sent", "bounced", "complained")


class RetentionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    never_contacted_days: int | None = Field(default=None, ge=30, le=3650)
    no_reply_days: int | None = Field(default=None, ge=30, le=3650)

    @property
    def enabled(self) -> bool:
        return self.never_contacted_days is not None or self.no_reply_days is not None

    @classmethod
    def load(cls, root: Path) -> RetentionPolicy:
        path = root / "retention.yaml"
        return cls.model_validate(yaml.safe_load(path.read_text()) or {}) if path.exists() else cls()


@dataclass
class RetentionRun:
    enabled: bool
    due: int = 0
    erased: int = 0
    prospect_ids: list[uuid.UUID] = field(default_factory=list)


def due_prospects(conn: Connection, tenant_id: uuid.UUID, policy: RetentionPolicy, now: datetime) -> list[uuid.UUID]:
    if not policy.enabled:
        return []
    nc = now - timedelta(days=policy.never_contacted_days) if policy.never_contacted_days is not None else None
    nr = now - timedelta(days=policy.no_reply_days) if policy.no_reply_days is not None else None
    rows = conn.execute(
        text("""
WITH ls AS (
  SELECT l.id, l.prospect_id, l.state, l.updated_at,
         (SELECT max(m.sent_at) FROM messages m WHERE m.lead_id = l.id AND m.status = ANY(:sent)) AS last_sent,
         EXISTS (SELECT 1 FROM replies r WHERE r.lead_id = l.id) AS replied,
         EXISTS (SELECT 1 FROM meetings mt WHERE mt.lead_id = l.id) AS met,
         EXISTS (SELECT 1 FROM messages q WHERE q.lead_id = l.id
                 AND q.status IN ('pending_approval','approved','queued','sending','failed_retryable',
                                  'needs_reconciliation')) AS in_flight
  FROM leads l WHERE l.tenant_id = :t
), expired AS (
  SELECT ls.*, (NOT replied AND NOT met AND NOT in_flight AND state = ANY(:states) AND (
           (last_sent IS NULL AND CAST(:nc AS timestamptz) IS NOT NULL AND updated_at < CAST(:nc AS timestamptz))
        OR (last_sent IS NOT NULL AND CAST(:nr AS timestamptz) IS NOT NULL AND last_sent < CAST(:nr AS timestamptz))
  )) AS is_expired FROM ls
)
SELECT p.id FROM prospects p
WHERE p.tenant_id = :t AND p.anonymised_at IS NULL
  AND EXISTS (SELECT 1 FROM expired e WHERE e.prospect_id = p.id)
  AND NOT EXISTS (SELECT 1 FROM expired e WHERE e.prospect_id = p.id AND NOT e.is_expired)
ORDER BY p.id"""),
        {"t": tenant_id, "sent": list(SENT), "states": list(EXPIRABLE), "nc": nc, "nr": nr},
    )
    return [r[0] for r in rows]


def run_retention(
    conn: Connection, tenant_id: uuid.UUID, policy: RetentionPolicy, now: datetime, *, apply: bool = False
) -> RetentionRun:
    out = RetentionRun(policy.enabled)
    if not policy.enabled:
        return out
    out.prospect_ids = due_prospects(conn, tenant_id, policy, now)
    out.due = len(out.prospect_ids)
    if not apply:
        return out
    for pid in out.prospect_ids:
        company = conn.execute(text("SELECT company_id FROM prospects WHERE id = :p"), {"p": pid}).scalar_one()
        anonymise_prospect(
            conn,
            prospect_id=pid,
            company_id=company,
            actor_id="retention",
            actor_type="system",
            reason="retention period ended",
        )
        out.erased += 1
    if out.erased:
        write_audit(
            conn,
            action="retention.erased",
            actor_type="system",
            actor_id="retention",
            tenant_id=tenant_id,
            after={"prospects": out.erased, "policy": policy.model_dump()},
        )
    return out
