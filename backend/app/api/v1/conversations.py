"""Phase 4 endpoints: escalations inbox, data-protection request tracker (incl. erasure), meetings (briefs,
attendance), and a dev-only inbound-reply injector for demos."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import CurrentUser, cipher_dep, require, settings_dep
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.masking import mask_name
from app.core.security import Permission, verify_totp
from app.db.session import get_engine, transaction
from app.domain.dpr import service as dpr
from app.domain.leads.state_machine import State, TransitionError, allowed, get_lead, transition
from app.domain.meetings import service as meetings
from app.settings import Settings

router = APIRouter(tags=["conversations"])
Operator = Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))]
Cipher = Annotated[PiiCipher, Depends(cipher_dep)]


# ------------------------------------------------------------------------------------------- escalations
class EscalationOut(BaseModel):
    id: str
    lead_id: str
    company: str
    reason: str
    detail: dict[str, object]
    pre_escalation_state: str | None
    lead_state: str
    status: str
    created_at: datetime
    allowed_targets: list[str]


SAFE_DETAIL_KEYS = {
    "reasons",
    "type",
    "why",
    "playbook_id",
    "booking_blocked",
    "dpr_id",
    "meeting_id",
    "agent",
    "detail",
    "tenant_contact_notified",
}


@router.get("/escalations", response_model=list[EscalationOut])
def escalations(
    user: Operator, tenant_id: uuid.UUID | None = None, status: Literal["open", "resolved", "all"] = "open"
) -> list[EscalationOut]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(
                text("""SELECT e.*, c.name AS company, l.state FROM escalations e JOIN leads l ON l.id = e.lead_id
              JOIN companies c ON c.id = l.company_id WHERE (:s = 'all' OR (:s = 'open' AND e.status <> 'resolved')
              OR (:s = 'resolved' AND e.status = 'resolved')) ORDER BY e.created_at DESC LIMIT 200"""),
                {"s": status},
            )
            .mappings()
            .all()
        )
    out = []
    for r in rows:
        pre = State(r["pre_escalation_state"]) if r["pre_escalation_state"] else None
        targets = sorted(s.value for s in State if r["state"] == "escalated" and allowed(State.ESCALATED, s, pre))
        out.append(
            EscalationOut(
                id=str(r["id"]),
                lead_id=str(r["lead_id"]),
                company=r["company"] or "[personal name]",
                reason=r["reason"],
                detail={k: v for k, v in (r["context"] or {}).items() if k in SAFE_DETAIL_KEYS},
                pre_escalation_state=r["pre_escalation_state"],
                lead_state=r["state"],
                status=r["status"],
                created_at=r["created_at"],
                allowed_targets=targets,
            )
        )
    return out


class ResolveIn(BaseModel):
    resolution: str = Field(min_length=3, max_length=1000)
    target_state: str | None = None


@router.post("/escalations/{escalation_id}/resolve")
def resolve_escalation(escalation_id: uuid.UUID, body: ResolveIn, user: Operator) -> dict[str, object]:
    with transaction(user.db_context(), get_engine()) as conn:
        e = (
            conn.execute(text("SELECT * FROM escalations WHERE id = :e FOR UPDATE"), {"e": escalation_id})
            .mappings()
            .first()
        )
        if e is None:
            raise ProblemError(404, "Escalation not found")
        if e["status"] == "resolved":
            raise ProblemError(409, "Already resolved")
        if body.target_state:
            lead = get_lead(conn, e["lead_id"])
            try:
                transition(
                    conn,
                    e["lead_id"],
                    State(body.target_state),
                    actor_type="user",
                    actor_id=str(user.id),
                    reason=f"escalation resolved: {body.resolution[:100]}",
                )
            except (TransitionError, ValueError) as exc:
                raise ProblemError(409, "Target state not allowed", f"{lead.state} -> {body.target_state}") from exc
        conn.execute(
            text(
                "UPDATE escalations SET status = 'resolved', resolved_at = now(), resolution = :r, "
                "assigned_to = :u WHERE id = :e"
            ),
            {"r": body.resolution, "u": user.id, "e": escalation_id},
        )
        write_audit(
            conn,
            action="escalation.resolved",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=e["tenant_id"],
            entity_type="escalation",
            entity_id=escalation_id,
            after={"target": body.target_state},
        )
    return {"status": "resolved"}


# ------------------------------------------------------------------------------------------- DP requests
class DprOut(BaseModel):
    id: str
    tenant_id: str
    lead_id: str | None
    company: str | None
    request_type: str
    regime: str
    controller: str
    status: str
    received_at: datetime
    deadline_at: datetime
    days_remaining: int
    overdue: bool
    response_draft: str | None
    completed_at: datetime | None


@router.get("/data-protection-requests", response_model=list[DprOut])
def dp_requests(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_COMPLIANCE_LOG))],
    cipher: Cipher,
    tenant_id: uuid.UUID | None = None,
) -> list[DprOut]:
    now = datetime.now(UTC)
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(
                text("""SELECT d.*, c.name AS company FROM data_protection_requests d LEFT JOIN leads l
              ON l.id = d.lead_id LEFT JOIN companies c ON c.id = l.company_id
              ORDER BY (d.status IN ('completed','refused_with_reason')), d.deadline_at LIMIT 500""")
            )
            .mappings()
            .all()
        )
        return [
            DprOut(
                id=str(r["id"]),
                tenant_id=str(r["tenant_id"]),
                lead_id=str(r["lead_id"]) if r["lead_id"] else None,
                company=r["company"],
                request_type=r["request_type"],
                regime=r["regime"],
                controller=r["controller"],
                status=r["status"],
                received_at=r["received_at"],
                deadline_at=r["deadline_at"],
                days_remaining=dpr.days_remaining(r["deadline_at"], now),
                overdue=r["deadline_at"] < now and r["status"] not in ("completed", "refused_with_reason"),
                response_draft=cipher.decrypt(conn, r["tenant_id"], "dpr.response_draft", r["response_draft_enc"])
                if r["response_draft_enc"]
                else None,
                completed_at=r["completed_at"],
            )
            for r in rows
        ]


class DprStatusIn(BaseModel):
    status: Literal["in_progress", "awaiting_tenant", "completed", "refused_with_reason"]
    note: str = Field(min_length=3, max_length=1000)


@router.post("/data-protection-requests/{dpr_id}/status")
def dp_status(dpr_id: uuid.UUID, body: DprStatusIn, user: Operator) -> dict[str, str]:
    with transaction(user.db_context(), get_engine()) as conn:
        r = (
            conn.execute(
                text("SELECT tenant_id, request_type, status FROM data_protection_requests WHERE id = :d FOR UPDATE"),
                {"d": dpr_id},
            )
            .mappings()
            .first()
        )
        if r is None:
            raise ProblemError(404, "Request not found")
        if body.status == "completed" and r["request_type"] == "erasure":
            raise ProblemError(409, "Use complete-erasure for erasure requests")
        conn.execute(
            text(
                "UPDATE data_protection_requests SET status = :s, handled_by = :u, completed_at = CASE WHEN :s = "
                "'completed' THEN now() ELSE completed_at END, responded_at = CASE WHEN :s IN ('completed',"
                "'refused_with_reason') THEN now() ELSE responded_at END WHERE id = :d"
            ),
            {"s": body.status, "u": user.id, "d": dpr_id},
        )
        write_audit(
            conn,
            action=f"dpr.{body.status}",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=r["tenant_id"],
            entity_type="data_protection_request",
            entity_id=dpr_id,
            after={"note": body.note},
        )
    return {"status": body.status}


class ErasureIn(BaseModel):
    totp_code: str = Field(min_length=6, max_length=10)  # destructive: MFA re-prompt
    identity_verified: Literal[True]  # operator attests the requester's identity was checked


@router.post("/data-protection-requests/{dpr_id}/complete-erasure")
def complete_erasure(dpr_id: uuid.UUID, body: ErasureIn, user: Operator, cipher: Cipher) -> dict[str, object]:
    with transaction(user.db_context(), get_engine()) as conn:
        enc = conn.execute(text("SELECT mfa_secret_enc FROM users WHERE id = :u"), {"u": user.id}).scalar()
        if not enc or not verify_totp(cipher.decrypt(conn, None, "users.mfa_secret", enc), body.totp_code):
            raise ProblemError(403, "MFA verification failed")
        r = (
            conn.execute(
                text("SELECT tenant_id, request_type, status FROM data_protection_requests WHERE id = :d"),
                {"d": dpr_id},
            )
            .mappings()
            .first()
        )
        if r is None:
            raise ProblemError(404, "Request not found")
        if r["request_type"] != "erasure" or r["status"] == "completed":
            raise ProblemError(409, "Only open erasure requests can be completed this way")
        counts = dpr.complete_erasure(conn, tenant_id=r["tenant_id"], dpr_id=dpr_id, user_id=user.id)
    return {"status": "completed", "erased": counts}


# ------------------------------------------------------------------------------------------- meetings
class MeetingOut(BaseModel):
    id: str
    lead_id: str
    company: str
    prospect: str | None
    starts_at: datetime
    ends_at: datetime
    status: str
    reschedule_count: int
    prospect_response: str | None
    lead_state: str


@router.get("/meetings", response_model=list[MeetingOut])
def list_meetings(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_LEADS))],
    cipher: Cipher,
    tenant_id: uuid.UUID | None = None,
) -> list[MeetingOut]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(
                text("""SELECT m.*, c.name AS company, p.name_enc, l.state FROM meetings m JOIN leads l
              ON l.id = m.lead_id JOIN companies c ON c.id = l.company_id LEFT JOIN prospects p ON p.id = l.prospect_id
              WHERE m.status <> 'proposed' ORDER BY m.starts_at DESC LIMIT 200""")
            )
            .mappings()
            .all()
        )
        return [
            MeetingOut(
                id=str(r["id"]),
                lead_id=str(r["lead_id"]),
                company=r["company"] or "[personal name]",
                prospect=mask_name(cipher.decrypt(conn, r["tenant_id"], "prospects.name", r["name_enc"]))
                if r["name_enc"]
                else None,
                starts_at=r["starts_at"],
                ends_at=r["ends_at"],
                status=r["status"],
                reschedule_count=r["reschedule_count"],
                prospect_response=r["prospect_response"],
                lead_state=r["state"],
            )
            for r in rows
        ]


@router.get("/meetings/{meeting_id}/brief")
def meeting_brief(
    meeting_id: uuid.UUID,
    request: Request,
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_LEADS))],
    cipher: Cipher,
) -> dict[str, str]:
    """The pre-meeting brief names the prospect, so opening it is logged as a PII access."""
    with transaction(user.db_context(), get_engine()) as conn:
        r = conn.execute(
            text("SELECT tenant_id, lead_id, brief_enc FROM meetings WHERE id = :m"), {"m": meeting_id}
        ).first()
        if r is None or r.brief_enc is None:
            raise ProblemError(404, "Brief not found")
        conn.execute(
            text("""INSERT INTO pii_access_log (user_id, tenant_id, lead_id, action, reason, ip_country)
              VALUES (:u, :t, :l, 'unmask', 'pre-meeting brief', :c)"""),
            {"u": user.id, "t": r.tenant_id, "l": r.lead_id, "c": request.headers.get("x-client-country")},
        )
        return {"brief": cipher.decrypt(conn, r.tenant_id, "meetings.brief", r.brief_enc)}


class AttendanceIn(BaseModel):
    status: Literal["attended", "no_show"]


@router.post("/meetings/{meeting_id}/attendance")
def attendance(
    meeting_id: uuid.UUID,
    body: AttendanceIn,
    user: Annotated[CurrentUser, Depends(require(Permission.CONFIRM_ATTENDANCE))],
) -> dict[str, str]:
    with transaction(user.db_context(), get_engine()) as conn:
        tenant_id = conn.execute(text("SELECT tenant_id FROM meetings WHERE id = :m"), {"m": meeting_id}).scalar()
        if tenant_id is None:
            raise ProblemError(404, "Meeting not found")
        source = "operator" if user.role == "operator" else "tenant_confirmation"
        try:
            meetings.record_attendance(
                conn,
                tenant_id=tenant_id,
                meeting_id=meeting_id,
                status=body.status,
                source=source,
                user_id=user.id,
                now=datetime.now(UTC),
            )
        except (ValueError, TransitionError) as exc:
            raise ProblemError(409, "Cannot record attendance", str(exc)) from exc
    return {"status": body.status}


# ------------------------------------------------------------------------------------------- dev: inbound injector
class InboundIn(BaseModel):
    tenant_id: uuid.UUID
    from_email: str = Field(max_length=320)
    body: str = Field(min_length=1, max_length=20000)
    subject: str = "Re: our conversation"
    in_reply_to: str | None = None


@router.post("/dev/inbound")
def dev_inbound(
    body: InboundIn,
    user: Operator,
    cipher: Cipher,
    settings: Annotated[Settings, Depends(settings_dep)],
    at: Annotated[datetime | None, Query()] = None,
) -> dict[str, object]:
    """DEV/TEST ONLY: simulate a prospect reply to see Agent 4 handle it (synthetic data)."""
    if settings.environment in ("staging", "prod"):
        raise ProblemError(404, "Not found")
    from app.adapters.inbound.base import InboundEmail
    from app.demo.seed import providers
    from app.workflows.pipeline import agent_context, process_inbound

    with transaction(user.db_context(body.tenant_id), get_engine()) as conn:
        campaign_id = conn.execute(text("SELECT id FROM campaigns ORDER BY created_at LIMIT 1")).scalar()
    if campaign_id is None:
        raise ProblemError(404, "Tenant has no campaign")
    ctx = agent_context(get_engine(), settings, cipher, body.tenant_id, campaign_id)
    msg = InboundEmail(
        f"<dev-{uuid.uuid4()}@inbound.test>",
        body.from_email,
        body.subject,
        body.body,
        at or datetime.now(UTC),
        body.in_reply_to,
    )
    out = process_inbound(ctx, [msg], providers(), now=at)[0]
    return {"classification": out.classification, "actions": out.actions, "lead_state": out.lead_state}
