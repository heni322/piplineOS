"""Lead state machine (docs/04-state-machine.md). The ONLY code that changes ``leads.state``.

Each transition: validated against the static table, optimistic-locked (version), audited in the same
transaction, and announced via the outbox. Guards that need compliance/sending live in their own services;
the database additionally refuses ``contacted`` without a message that passed the send gate.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import ActorType, write_audit


class State(StrEnum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    COMPLIANCE_CHECKED = "compliance_checked"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    CONTACTED = "contacted"
    REPLIED = "replied"
    QUALIFIED = "qualified"
    BOOKED = "booked"
    CONFIRMED = "confirmed"
    ATTENDED = "attended"
    BILLED = "billed"
    REJECTED_BY_COMPLIANCE = "rejected_by_compliance"
    ALTERNATIVE_CHANNEL = "alternative_channel"
    SUPPRESSED = "suppressed"
    OBJECTED = "objected"
    ERASED = "erased"
    DISQUALIFIED = "disqualified"
    RESCHEDULED = "rescheduled"
    NO_SHOW = "no_show"
    DISPUTED = "disputed"
    ESCALATED = "escalated"


S = State
TERMINAL = frozenset({S.REJECTED_BY_COMPLIANCE, S.SUPPRESSED, S.OBJECTED, S.ERASED, S.DISQUALIFIED})
NON_TERMINAL = frozenset(State) - TERMINAL

_T: dict[State, frozenset[State]] = {
    S.DISCOVERED: frozenset({S.ENRICHED, S.DISQUALIFIED}),
    S.ENRICHED: frozenset({S.COMPLIANCE_CHECKED, S.REJECTED_BY_COMPLIANCE, S.ALTERNATIVE_CHANNEL}),
    S.COMPLIANCE_CHECKED: frozenset({S.PENDING_APPROVAL, S.ESCALATED, S.REJECTED_BY_COMPLIANCE, S.ALTERNATIVE_CHANNEL}),
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.DISQUALIFIED, S.REJECTED_BY_COMPLIANCE, S.ALTERNATIVE_CHANNEL}),
    S.APPROVED: frozenset({S.CONTACTED, S.REJECTED_BY_COMPLIANCE, S.ALTERNATIVE_CHANNEL, S.ESCALATED}),
    S.CONTACTED: frozenset({S.REPLIED, S.DISQUALIFIED}),
    S.REPLIED: frozenset({S.OBJECTED, S.QUALIFIED, S.CONTACTED, S.DISQUALIFIED, S.ESCALATED}),
    S.QUALIFIED: frozenset({S.BOOKED, S.ESCALATED, S.OBJECTED, S.DISQUALIFIED}),
    S.BOOKED: frozenset({S.CONFIRMED, S.RESCHEDULED, S.ATTENDED, S.NO_SHOW, S.ESCALATED}),
    S.CONFIRMED: frozenset({S.RESCHEDULED, S.ATTENDED, S.NO_SHOW, S.ESCALATED}),
    S.RESCHEDULED: frozenset({S.BOOKED, S.ESCALATED}),
    S.NO_SHOW: frozenset({S.RESCHEDULED, S.DISQUALIFIED}),
    S.ATTENDED: frozenset({S.BILLED, S.DISPUTED}),
    S.BILLED: frozenset({S.DISPUTED}),
    S.DISPUTED: frozenset({S.BILLED, S.ATTENDED}),
    S.ALTERNATIVE_CHANNEL: frozenset({S.DISQUALIFIED}),
    S.ESCALATED: frozenset(),  # resolved by a human to a target valid from the pre-escalation state
}
# Global rules: any non-terminal state may be suppressed; any state (except erased itself) may be erased;
# an objection/unsubscribe before a meeting exists ends the lead. After booking, an objection keeps the meeting
# (flag objected_after_booking) and only stops follow-ups — see record_objection().
PRE_MEETING = frozenset(
    {
        S.DISCOVERED,
        S.ENRICHED,
        S.COMPLIANCE_CHECKED,
        S.PENDING_APPROVAL,
        S.APPROVED,
        S.CONTACTED,
        S.REPLIED,
        S.QUALIFIED,
        S.ALTERNATIVE_CHANNEL,
        S.ESCALATED,
        S.NO_SHOW,
    }
)
for _s in NON_TERMINAL:
    _T[_s] = _T.get(_s, frozenset()) | {S.SUPPRESSED, S.ERASED}
for _s in PRE_MEETING:
    _T[_s] = _T[_s] | {S.OBJECTED}
for _s in TERMINAL - {S.ERASED}:
    _T[_s] = frozenset({S.ERASED})
_T[S.ERASED] = frozenset()
TRANSITIONS: dict[State, frozenset[State]] = _T


class TransitionError(Exception):
    pass


class ConcurrentModification(TransitionError):
    pass


def allowed(src: State, dst: State, pre_escalation: State | None = None) -> bool:
    if src == S.ESCALATED:
        if dst in (S.SUPPRESSED, S.ERASED, S.OBJECTED):
            return True
        return pre_escalation is not None and (dst == pre_escalation or dst in TRANSITIONS[pre_escalation])
    return dst in TRANSITIONS[src]


@dataclass(frozen=True)
class LeadRow:
    id: uuid.UUID
    tenant_id: uuid.UUID
    state: State
    version: int
    pre_escalation_state: State | None


def get_lead(conn: Connection, lead_id: uuid.UUID, *, for_update: bool = False) -> LeadRow:
    sql = "SELECT id, tenant_id, state, version, pre_escalation_state FROM leads WHERE id = :id"
    row = conn.execute(text(sql + (" FOR UPDATE" if for_update else "")), {"id": lead_id}).mappings().first()
    if row is None:
        raise TransitionError(f"lead {lead_id} not found in this tenant context")
    pre = row["pre_escalation_state"]
    return LeadRow(row["id"], row["tenant_id"], State(row["state"]), row["version"], State(pre) if pre else None)


def transition(
    conn: Connection,
    lead_id: uuid.UUID,
    to: State,
    *,
    actor_type: ActorType,
    actor_id: str,
    reason: str,
    expected_version: int | None = None,
) -> LeadRow:
    lead = get_lead(conn, lead_id)
    if expected_version is not None and lead.version != expected_version:
        raise ConcurrentModification(f"lead {lead_id}: expected version {expected_version}, found {lead.version}")
    if lead.state == to:
        return lead  # idempotent no-op (retries)
    if not allowed(lead.state, to, lead.pre_escalation_state):
        raise TransitionError(f"illegal transition {lead.state} -> {to}")
    pre = lead.state.value if to == S.ESCALATED else None
    res = conn.execute(
        text("""UPDATE leads SET state = :to, state_changed_at = now(), version = version + 1,
                  pre_escalation_state = CASE WHEN :to = 'escalated' THEN :pre ELSE NULL END
                WHERE id = :id AND version = :v"""),
        {"to": to.value, "pre": pre, "id": lead_id, "v": lead.version},
    )
    if res.rowcount != 1:
        raise ConcurrentModification(f"lead {lead_id} changed concurrently")
    write_audit(
        conn,
        action="lead.transition",
        actor_type=actor_type,
        actor_id=actor_id,
        tenant_id=lead.tenant_id,
        entity_type="lead",
        entity_id=lead_id,
        before={"state": lead.state.value},
        after={"state": to.value, "reason": reason},
    )
    conn.execute(
        text("INSERT INTO outbox (tenant_id, topic, payload) VALUES (:t, 'lead.transitioned', CAST(:p AS jsonb))"),
        {"t": lead.tenant_id, "p": json.dumps({"lead_id": str(lead_id), "from": lead.state.value, "to": to.value})},
    )
    return LeadRow(lead.id, lead.tenant_id, to, lead.version + 1, State(pre) if pre else None)


def record_objection(
    conn: Connection, lead_id: uuid.UUID, *, actor_type: ActorType, actor_id: str, reason: str
) -> LeadRow:
    """Unsubscribe/objection. Before a meeting: lead -> objected. After booking: keep the meeting, flag it,
    cancel pending follow-ups (the caller has already written the global suppression)."""
    lead = get_lead(conn, lead_id, for_update=True)
    conn.execute(
        text(
            "UPDATE messages SET status = 'cancelled' WHERE lead_id = :l AND status IN "
            "('draft','pending_approval','approved','queued','failed_retryable')"
        ),
        {"l": lead_id},
    )
    conn.execute(
        text("UPDATE scheduled_actions SET status = 'cancelled' WHERE lead_id = :l AND status = 'pending'"),
        {"l": lead_id},
    )
    if lead.state in PRE_MEETING:
        return transition(conn, lead_id, S.OBJECTED, actor_type=actor_type, actor_id=actor_id, reason=reason)
    if lead.state in TERMINAL:
        return lead
    conn.execute(
        text("UPDATE leads SET objected_after_booking = true, version = version + 1 WHERE id = :l"), {"l": lead_id}
    )
    write_audit(
        conn,
        action="lead.objected_after_booking",
        actor_type=actor_type,
        actor_id=actor_id,
        tenant_id=lead.tenant_id,
        entity_type="lead",
        entity_id=lead_id,
        after={"reason": reason},
    )
    return LeadRow(lead.id, lead.tenant_id, lead.state, lead.version + 1, lead.pre_escalation_state)
