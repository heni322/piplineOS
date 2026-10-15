"""Operator controls that must be safe to press at any time: pause a client (the emergency stop), pause a campaign,
and the pilot contact limit.

A pause HOLDS work, never destroys it: queued messages stay queued, leads in flight keep their state, and resuming
continues exactly where it stopped. The outbound gateway and the lead pipeline both honour it (gateway: before the
compliance check, so a pause never reads as a compliance rejection).

The pilot limit caps how many NEW people a campaign may ever email (first emails only). Follow-ups to people already
contacted, replies and meeting reminders continue. Default 100 for new campaigns; campaigns that existed before
migration 0019 were left unlimited, so a deploy never silently stops a running campaign."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit

PILOT_MAX = 100_000
# Clients whose replies, bounces and complaints are still handled. A paused client's SENDING is held (gateway,
# send_due), but an unsubscribe or objection that arrives during a pause must be honoured at once, before any
# held follow-up can go out after resuming.
LISTENING_STATUSES = ("active", "paused")
# A lead counts as "contacted by this campaign" once its first email left (or was recorded in dry-run).
CONTACTED_STATUSES = ("sending", "sent", "dry_run_sent", "needs_reconciliation", "bounced", "complained")


class ControlError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PilotUsage:
    limit: int | None
    used: int

    @property
    def reached(self) -> bool:
        return self.limit is not None and self.used >= self.limit


def contacts_used(conn: Connection, campaign_id: uuid.UUID) -> int:
    """Distinct people this campaign has emailed a first message to."""
    return int(
        conn.execute(
            text("""SELECT count(DISTINCT lead_id) FROM messages WHERE campaign_id = :c AND kind = 'outreach'
                    AND sequence_step = 0 AND status = ANY(:s)"""),
            {"c": campaign_id, "s": list(CONTACTED_STATUSES)},
        ).scalar_one()
    )


def pilot_usage(conn: Connection, campaign_id: uuid.UUID) -> PilotUsage:
    limit = conn.execute(
        text("SELECT pilot_max_contacts FROM campaigns WHERE id = :c"), {"c": campaign_id}
    ).scalar_one()
    return PilotUsage(limit, contacts_used(conn, campaign_id))


def is_held(conn: Connection, tenant_id: uuid.UUID, campaign_id: uuid.UUID | None) -> str | None:
    """The hold reason for new work on this client/campaign, or None. Used by the pipeline before AI work: a paused
    client or campaign, or a campaign whose pilot limit is reached."""
    status = conn.execute(text("SELECT status FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar()
    if status == "paused":
        return "CLIENT_PAUSED"
    if campaign_id is not None:
        c = conn.execute(
            text("SELECT status, pilot_max_contacts FROM campaigns WHERE id = :c"), {"c": campaign_id}
        ).first()
        if c is not None and c.status in ("paused", "auto_paused"):
            return "CAMPAIGN_PAUSED"
        # A full pilot: drafting more first emails would only spend AI and review time on emails that can't be
        # sent. New leads wait (state unchanged) and continue if the operator raises the limit.
        if (
            c is not None
            and c.pilot_max_contacts is not None
            and contacts_used(conn, campaign_id) >= c.pilot_max_contacts
        ):
            return "PILOT_LIMIT_REACHED"
    return None


def _audit(
    conn: Connection,
    action: str,
    operator_id: uuid.UUID,
    tenant_id: uuid.UUID,
    entity: str,
    entity_id: uuid.UUID,
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    write_audit(
        conn,
        action=action,
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type=entity,
        entity_id=entity_id,
        before=before,
        after=after,
    )


def pause_client(conn: Connection, *, tenant_id: uuid.UUID, operator_id: uuid.UUID, reason: str) -> None:
    """Emergency stop for one client. Only an active client can be paused."""
    reason = reason.strip()
    if not reason:
        raise ControlError("REASON_REQUIRED")
    done = conn.execute(
        text("""UPDATE tenants SET status = 'paused', paused_reason = :r, paused_at = now()
                WHERE id = :t AND status = 'active' RETURNING id"""),
        {"t": tenant_id, "r": reason[:500]},
    ).first()
    if done is None:
        raise ControlError("NOT_ACTIVE")
    held = conn.execute(
        text(
            "SELECT count(*) FROM messages WHERE tenant_id = :t AND status IN ('approved','queued','failed_retryable')"
        ),
        {"t": tenant_id},
    ).scalar_one()
    _audit(
        conn,
        "tenant.paused",
        operator_id,
        tenant_id,
        "tenant",
        tenant_id,
        {"status": "active"},
        {"status": "paused", "reason": reason[:500], "messages_held": int(held)},
    )


def resume_client(conn: Connection, *, tenant_id: uuid.UUID, operator_id: uuid.UUID) -> None:
    done = conn.execute(
        text("""UPDATE tenants SET status = 'active', paused_reason = NULL, paused_at = NULL
                WHERE id = :t AND status = 'paused' RETURNING id"""),
        {"t": tenant_id},
    ).first()
    if done is None:
        raise ControlError("NOT_PAUSED")
    _audit(
        conn, "tenant.resumed", operator_id, tenant_id, "tenant", tenant_id, {"status": "paused"}, {"status": "active"}
    )


def _campaign_tenant(conn: Connection, campaign_id: uuid.UUID) -> uuid.UUID:
    tid = conn.execute(text("SELECT tenant_id FROM campaigns WHERE id = :c"), {"c": campaign_id}).scalar()
    if tid is None:
        raise ControlError("NOT_FOUND")
    return uuid.UUID(str(tid))


def pause_campaign(conn: Connection, *, campaign_id: uuid.UUID, operator_id: uuid.UUID, reason: str) -> None:
    reason = reason.strip()
    if not reason:
        raise ControlError("REASON_REQUIRED")
    tid = _campaign_tenant(conn, campaign_id)
    done = conn.execute(
        text("""UPDATE campaigns SET status = 'paused', paused_reason = :r WHERE id = :c AND status = 'active'
                RETURNING id"""),
        {"c": campaign_id, "r": reason[:500]},
    ).first()
    if done is None:
        raise ControlError("NOT_ACTIVE")
    _audit(
        conn,
        "campaign.paused",
        operator_id,
        tid,
        "campaign",
        campaign_id,
        {"status": "active"},
        {"status": "paused", "reason": reason[:500]},
    )


def resume_campaign(conn: Connection, *, campaign_id: uuid.UUID, operator_id: uuid.UUID) -> None:
    """Resumes a manual or automatic pause. An automatic (deliverability) pause is resumed only by a person."""
    tid = _campaign_tenant(conn, campaign_id)
    before = conn.execute(
        text("""UPDATE campaigns c SET status = 'active', paused_reason = NULL FROM (SELECT status FROM campaigns
                WHERE id = :c) old WHERE c.id = :c AND c.status IN ('paused','auto_paused') RETURNING old.status"""),
        {"c": campaign_id},
    ).scalar()
    if before is None:
        raise ControlError("NOT_PAUSED")
    _audit(
        conn, "campaign.resumed", operator_id, tid, "campaign", campaign_id, {"status": before}, {"status": "active"}
    )


def set_pilot_limit(
    conn: Connection, *, campaign_id: uuid.UUID, operator_id: uuid.UUID, max_contacts: int | None
) -> PilotUsage:
    """None removes the limit (full volume). Lowering below what is already used is allowed: it just holds."""
    if max_contacts is not None and not 1 <= max_contacts <= PILOT_MAX:
        raise ControlError("OUT_OF_RANGE")
    tid = _campaign_tenant(conn, campaign_id)
    old = conn.execute(
        text("SELECT pilot_max_contacts FROM campaigns WHERE id = :c FOR UPDATE"), {"c": campaign_id}
    ).scalar()
    conn.execute(
        text("UPDATE campaigns SET pilot_max_contacts = :m WHERE id = :c"), {"m": max_contacts, "c": campaign_id}
    )
    _audit(
        conn,
        "campaign.pilot_limit_set",
        operator_id,
        tid,
        "campaign",
        campaign_id,
        {"pilot_max_contacts": old},
        {"pilot_max_contacts": max_contacts},
    )
    return pilot_usage(conn, campaign_id)
