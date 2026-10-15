"""Operator endpoints for the vertical slice: approval queue, pipeline summary, compliance log, dry-run log,
and (dev/demo scale) synchronous pipeline / send triggers. Prospect names are masked unless explicitly unmasked
(logged in pii_access_log)."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection, RowMapping

from app.api.deps import CurrentUser, cipher_dep, require, settings_dep
from app.api.v1.control_errors import problem_for
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.idempotency import run_idempotent
from app.core.masking import mask_email, mask_name
from app.core.security import Permission
from app.db.session import get_engine, transaction
from app.domain.approvals import service as approvals
from app.domain.tenants import controls
from app.settings import Settings

router = APIRouter(tags=["operations"])
Operator = Annotated[CurrentUser, Depends(require(Permission.APPROVE_MESSAGES))]
Cipher = Annotated[PiiCipher, Depends(cipher_dep)]


# ---------------------------------------------------------------------------------------------- models
class Claim(BaseModel):
    text: str
    source_url: str


class SequenceMessage(BaseModel):
    message_id: str
    step: int
    subject: str
    body: str


class ComplianceInfo(BaseModel):
    outcome: str
    reason_codes: list[str]
    rules_applied: dict[str, str]
    decided_at: datetime


class ApprovalItem(BaseModel):
    message_id: str
    lead_id: str
    tenant_id: str
    company: str
    prospect_name: str
    prospect_title: str | None
    prospect_country: str | None
    signal_summary: str | None
    signal_url: str | None
    relevance_score: int | None
    subject_options: list[str]
    sequence: list[SequenceMessage]
    footer_preview: str
    claims: list[Claim]
    compliance: ComplianceInfo | None
    quality_score: float | None
    critique: dict[str, object] | None
    created_at: datetime
    masked: bool
    kind: str = "outreach"  # outreach | agent_reply (incl. slot offers)
    prospect_reply: str | None = None  # the reply this answers (untrusted text, masked by default)


class ApprovalPage(BaseModel):
    items: list[ApprovalItem]
    total_pending: int


class EditIn(BaseModel):
    message_id: uuid.UUID
    body: str = Field(min_length=10, max_length=3000)
    subject: str | None = Field(default=None, max_length=120)


class DecisionIn(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    edits: list[EditIn] = []
    reason: str | None = Field(default=None, max_length=1000)
    review_seconds: int | None = Field(default=None, ge=0, le=86400)


# ---------------------------------------------------------------------------------------------- helpers
def _mask_in_text(text_: str, name: str) -> str:
    out = text_
    for part in sorted(set(name.split()), key=len, reverse=True):
        if len(part) > 1:
            out = re.sub(rf"\b{re.escape(part)}\b", mask_name(part) or "***", out)
    return out


def _approval_item(
    conn: Connection, cipher: PiiCipher, head: RowMapping, public_base: str, unmask: bool
) -> ApprovalItem:
    t = head["tenant_id"]
    lead_id = head["lead_id"]
    name = cipher.decrypt(conn, t, "prospects.name", head["name_enc"])
    seq_rows = (
        conn.execute(
            text(
                "SELECT id, sequence_step, subject_enc, body_enc FROM messages WHERE lead_id = :l "
                "AND status = 'pending_approval' AND kind = :k AND (:k = 'outreach' OR id = :id) "
                "ORDER BY sequence_step"
            ),
            {"l": lead_id, "k": head["kind"], "id": head["id"]},
        )
        .mappings()
        .all()
    )
    seq = []
    for m in seq_rows:
        subject = cipher.decrypt(conn, t, "messages.subject", m["subject_enc"])
        body = cipher.decrypt(conn, t, "messages.body", m["body_enc"])
        if not unmask:
            subject, body = _mask_in_text(subject, name), _mask_in_text(body, name)
        seq.append(SequenceMessage(message_id=str(m["id"]), step=m["sequence_step"], subject=subject, body=body))
    opts = (
        json.loads(cipher.decrypt(conn, t, "messages.subject_options", head["subject_options_enc"]))
        if head["subject_options_enc"]
        else []
    )
    claims = conn.execute(
        text("SELECT DISTINCT claim_text, source_url FROM message_personalization_sources WHERE message_id = :m"),
        {"m": head["id"]},
    ).all()
    d = conn.execute(
        text(
            "SELECT outcome, reason_codes, rules_applied, created_at FROM compliance_decisions WHERE "
            "lead_id = :l ORDER BY created_at DESC LIMIT 1"
        ),
        {"l": lead_id},
    ).first()
    reply_text = None
    if head["kind"] != "outreach":
        enc = conn.execute(
            text(
                "SELECT body_text_enc FROM replies WHERE lead_id = :l AND body_text_enc IS NOT NULL AND "
                "(id = :r OR :r IS NULL) ORDER BY received_at DESC LIMIT 1"
            ),
            {"l": lead_id, "r": head["reply_id"]},
        ).scalar()
        if enc:
            reply_text = cipher.decrypt(conn, t, "replies.body", enc)
            reply_text = reply_text if unmask else _mask_in_text(reply_text, name)
    company = head["company"] or "[personal name]"
    footer = str(head["compliance_footer"] or "").replace("{unsubscribe_url}", f"{public_base}/u/<signed-link-at-send>")
    return ApprovalItem(
        message_id=str(head["id"]),
        lead_id=str(lead_id),
        tenant_id=str(t),
        company=str(company),
        prospect_name=name if unmask else (mask_name(name) or ""),
        prospect_title=head["title"],
        prospect_country=head["country_code"],
        signal_summary=head["signal_summary"],
        signal_url=head["source_url"],
        relevance_score=head["relevance_score"],
        subject_options=[o if unmask else _mask_in_text(o, name) for o in opts],
        sequence=seq,
        footer_preview=footer,
        claims=[
            Claim(text=c.claim_text if unmask else _mask_in_text(c.claim_text, name), source_url=c.source_url)
            for c in claims
        ],
        compliance=ComplianceInfo(
            outcome=d.outcome, reason_codes=d.reason_codes, rules_applied=d.rules_applied, decided_at=d.created_at
        )
        if d
        else None,
        quality_score=float(head["quality_score"]) if head["quality_score"] is not None else None,
        critique=head["critique"],
        created_at=head["created_at"],
        masked=not unmask,
        kind=head["kind"],
        prospect_reply=reply_text,
    )


HEAD_SQL = """SELECT m.id, m.tenant_id, m.lead_id, m.kind, m.reply_id, m.subject_options_enc, m.compliance_footer,
       m.quality_score,
       m.critique, m.created_at, p.name_enc, p.title, p.country_code, c.name AS company, s.signal_summary, s.source_url,
       s.relevance_score
FROM messages m JOIN leads l ON l.id = m.lead_id JOIN prospects p ON p.id = l.prospect_id
JOIN companies c ON c.id = l.company_id LEFT JOIN signals s ON s.id = l.signal_id
WHERE m.status = 'pending_approval' AND m.sequence_step = 0"""


# ---------------------------------------------------------------------------------------------- approvals
@router.get("/approvals", response_model=ApprovalPage)
def approval_queue(
    user: Operator,
    cipher: Cipher,
    settings: Annotated[Settings, Depends(settings_dep)],
    tenant_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ApprovalPage:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        heads = conn.execute(text(HEAD_SQL + " ORDER BY m.created_at LIMIT :n"), {"n": limit}).mappings().all()
        total = conn.execute(
            text("SELECT count(*) FROM messages WHERE status = 'pending_approval' AND sequence_step = 0")
        ).scalar_one()
        items = [_approval_item(conn, cipher, h, settings.public_base_url, unmask=False) for h in heads]
    return ApprovalPage(items=items, total_pending=total)


@router.get("/approvals/{message_id}", response_model=ApprovalItem)
def approval_detail(
    message_id: uuid.UUID,
    request: Request,
    user: Operator,
    cipher: Cipher,
    settings: Annotated[Settings, Depends(settings_dep)],
    unmask_reason: Annotated[str | None, Query(min_length=3, max_length=500)] = None,
) -> ApprovalItem:
    """With unmask_reason: returns names in clear (needed before editing) and logs the access first."""
    with transaction(user.db_context(), get_engine()) as conn:
        head = conn.execute(text(HEAD_SQL + " AND m.id = :id"), {"id": message_id}).mappings().first()
        if head is None:
            raise ProblemError(404, "Pending message not found")
        if unmask_reason:
            conn.execute(
                text("""INSERT INTO pii_access_log (user_id, tenant_id, lead_id, action, reason, ip_country)
                  VALUES (:u, :t, :l, 'unmask', :r, :c)"""),
                {
                    "u": user.id,
                    "t": head["tenant_id"],
                    "l": head["lead_id"],
                    "r": unmask_reason,
                    "c": request.headers.get("x-client-country"),
                },
            )
        return _approval_item(conn, cipher, head, settings.public_base_url, unmask=bool(unmask_reason))


@router.post("/approvals/{message_id}/decision")
def approval_decision(
    message_id: uuid.UUID,
    body: DecisionIn,
    user: Operator,
    cipher: Cipher,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    with transaction(user.db_context(), get_engine()) as conn:
        tenant_id = conn.execute(text("SELECT tenant_id FROM messages WHERE id = :m"), {"m": message_id}).scalar()
        if tenant_id is None:
            raise ProblemError(404, "Message not found")

        def act() -> tuple[int, dict[str, object]]:
            try:
                result = approvals.decide(
                    conn,
                    cipher,
                    tenant_id=tenant_id,
                    head_message_id=message_id,
                    reviewer_id=user.id,
                    decision=body.decision,
                    reason=body.reason,
                    review_seconds=body.review_seconds,
                    edits=[approvals.Edit(e.message_id, e.body, e.subject) for e in body.edits],
                )
            except approvals.ApprovalError as exc:
                raise ProblemError(409, "Cannot apply decision", str(exc)) from exc
            return 200, result

        status, payload = run_idempotent(
            conn,
            tenant_id=tenant_id,
            scope=f"approval:{message_id}",
            key=idempotency_key,
            request=body.model_dump(mode="json"),
            fn=act,
        )
    return JSONResponse(payload, status_code=status)


# ---------------------------------------------------------------------------------------------- dashboards
class TenantOut(BaseModel):
    id: str
    slug: str
    legal_name: str
    status: str
    live_mode: bool
    approval_mode: str


@router.get("/tenants", response_model=list[TenantOut])
def tenants(user: Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]) -> list[TenantOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = (
            conn.execute(
                text("SELECT id, slug, legal_name, status, live_mode, approval_mode FROM tenants ORDER BY slug")
            )
            .mappings()
            .all()
        )
    return [TenantOut(**{**r, "id": str(r["id"])}) for r in rows]


class PipelineSummary(BaseModel):
    by_state: dict[str, int]
    pending_approval: int
    open_escalations: int
    open_alternative_tasks: int
    dry_run_sends: int
    real_sends: int
    llm_calls: dict[str, int]


@router.get("/pipeline/summary", response_model=PipelineSummary)
def pipeline_summary(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_LEADS))], tenant_id: uuid.UUID | None = None
) -> PipelineSummary:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        states = {r.state: r.n for r in conn.execute(text("SELECT state, count(*) AS n FROM leads GROUP BY state"))}

        def q(sql: str) -> int:
            return int(conn.execute(text(sql)).scalar_one())

        llm = {r.model: r.n for r in conn.execute(text("SELECT model, count(*) AS n FROM llm_calls GROUP BY model"))}
        return PipelineSummary(
            by_state=states,
            pending_approval=q("SELECT count(*) FROM messages WHERE status = 'pending_approval' AND sequence_step = 0"),
            open_escalations=q("SELECT count(*) FROM escalations WHERE status <> 'resolved'"),
            open_alternative_tasks=q("SELECT count(*) FROM alternative_channel_tasks WHERE status = 'open'"),
            dry_run_sends=q("SELECT count(*) FROM messages WHERE status = 'dry_run_sent'"),
            real_sends=q(
                "SELECT count(*) FROM messages WHERE status IN ('sent','bounced','complained') "
                "AND mode_at_send = 'live'"
            ),
            llm_calls=llm,
        )


class ComplianceLogRow(BaseModel):
    decided_at: datetime
    lead_id: str
    company: str
    prospect: str | None
    stage: str
    outcome: str
    reason_codes: list[str]
    rules_applied: dict[str, str]


@router.get("/compliance/log", response_model=list[ComplianceLogRow])
def compliance_log(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_COMPLIANCE_LOG))],
    cipher: Cipher,
    tenant_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ComplianceLogRow]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(
                text("""SELECT d.created_at, d.lead_id, d.tenant_id, d.stage, d.outcome, d.reason_codes,
              d.rules_applied, c.name AS company, p.name_enc FROM compliance_decisions d
              JOIN leads l ON l.id = d.lead_id
              JOIN companies c ON c.id = l.company_id LEFT JOIN prospects p ON p.id = l.prospect_id
              ORDER BY d.created_at DESC LIMIT :n"""),
                {"n": limit},
            )
            .mappings()
            .all()
        )
        return [
            ComplianceLogRow(
                decided_at=r["created_at"],
                lead_id=str(r["lead_id"]),
                company=r["company"] or "[personal name]",
                prospect=mask_name(cipher.decrypt(conn, r["tenant_id"], "prospects.name", r["name_enc"]))
                if r["name_enc"]
                else None,
                stage=r["stage"],
                outcome=r["outcome"],
                reason_codes=r["reason_codes"],
                rules_applied=r["rules_applied"],
            )
            for r in rows
        ]


class DryRunRow(BaseModel):
    created_at: datetime
    effect_type: str
    adapter: str
    recipient: str | None
    subject: str | None
    idempotency_key: str


@router.get("/dry-run", response_model=list[DryRunRow])
def dry_run_log(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))],
    cipher: Cipher,
    tenant_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[DryRunRow]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(text("SELECT * FROM dry_run_log ORDER BY created_at DESC LIMIT :n"), {"n": limit})
            .mappings()
            .all()
        )
        out = []
        for r in rows:
            payload = (
                json.loads(cipher.decrypt(conn, r["tenant_id"], "dry_run_log.payload", r["payload_enc"]))
                if r["payload_enc"]
                else {}
            )
            out.append(
                DryRunRow(
                    created_at=r["created_at"],
                    effect_type=r["effect_type"],
                    adapter=r["adapter"],
                    recipient=mask_email(payload.get("to")),
                    subject=payload.get("subject"),
                    idempotency_key=r["idempotency_key"],
                )
            )
        return out


# ---------------------------------------------------------------------------------------------- demo-scale triggers
class RunIn(BaseModel):
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID


@router.post("/pipeline/run")
def run_pipeline(
    body: RunIn,
    user: Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))],
    cipher: Cipher,
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, object]:
    """Runs the campaign synchronously. Demo/dev scale only; production uses the Celery workers."""
    if settings.environment in ("staging", "prod"):
        raise ProblemError(409, "Use the workers in this environment")
    from app.adapters.registry import providers_for
    from app.workflows.pipeline import agent_context, run_campaign

    ctx = agent_context(get_engine(), settings, cipher, body.tenant_id, body.campaign_id)
    run = run_campaign(ctx, providers_for(settings, ctx))
    return {"discovered": run.hunt.discovered, "deduplicated": run.hunt.deduplicated, "outcomes": run.outcomes}


class SendIn(BaseModel):
    tenant_id: uuid.UUID


@router.post("/send/run")
def run_send(
    body: SendIn, user: Operator, cipher: Cipher, settings: Annotated[Settings, Depends(settings_dep)]
) -> dict[str, object]:
    """Sends due approved messages through the outbound gateway (dry-run unless all live keys are on)."""
    from app.workflows.pipeline import send_due

    res = send_due(get_engine(), settings, cipher, body.tenant_id)
    counts: dict[str, int] = {}
    for r in res:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {"results": counts, "modes": sorted({r.mode for r in res if r.mode})}


class CampaignOut(BaseModel):
    id: str
    tenant_id: str
    tenant_name: str
    name: str
    status: str
    daily_cap: int
    target_countries: list[str]
    paused_reason: str | None = None
    pilot_max_contacts: int | None = None
    pilot_contacts_used: int = 0


@router.get("/campaigns", response_model=list[CampaignOut])
def campaigns(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))], tenant_id: uuid.UUID | None = None
) -> list[CampaignOut]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(
                text("""SELECT c.*, t.legal_name, (SELECT count(DISTINCT m.lead_id) FROM messages m
                     WHERE m.campaign_id = c.id AND m.kind = 'outreach' AND m.sequence_step = 0
                     AND m.status = ANY(:contacted)) AS used
              FROM campaigns c JOIN tenants t ON t.id = c.tenant_id
              WHERE CAST(:t AS uuid) IS NULL OR c.tenant_id = :t ORDER BY t.legal_name, c.name"""),
                {"t": tenant_id, "contacted": list(controls.CONTACTED_STATUSES)},
            )
            .mappings()
            .all()
        )
    return [
        CampaignOut(
            id=str(r["id"]),
            tenant_id=str(r["tenant_id"]),
            tenant_name=r["legal_name"],
            name=r["name"],
            status=r["status"],
            daily_cap=r["daily_cap"],
            target_countries=list(r["target_countries"]),
            paused_reason=r["paused_reason"],
            pilot_max_contacts=r["pilot_max_contacts"],
            pilot_contacts_used=r["used"],
        )
        for r in rows
    ]


def _campaign(conn: Connection, campaign_id: uuid.UUID) -> CampaignOut:
    r = (
        conn.execute(
            text("""SELECT c.*, t.legal_name FROM campaigns c JOIN tenants t ON t.id = c.tenant_id
                    WHERE c.id = :c"""),
            {"c": campaign_id},
        )
        .mappings()
        .first()
    )
    if r is None:
        raise ProblemError(404, "Campaign not found")
    return CampaignOut(
        id=str(r["id"]),
        tenant_id=str(r["tenant_id"]),
        tenant_name=r["legal_name"],
        name=r["name"],
        status=r["status"],
        daily_cap=r["daily_cap"],
        target_countries=list(r["target_countries"]),
        paused_reason=r["paused_reason"],
        pilot_max_contacts=r["pilot_max_contacts"],
        pilot_contacts_used=controls.contacts_used(conn, campaign_id),
    )


class CampaignPauseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PilotLimitIn(BaseModel):
    max_contacts: int | None = Field(default=None, ge=1, le=controls.PILOT_MAX)


CampaignAdmin = Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]


def _campaign_control(campaign_id: uuid.UUID, user: CurrentUser, act: Callable[[Connection], object]) -> CampaignOut:
    try:
        with transaction(user.db_context(), get_engine()) as conn:
            act(conn)
            return _campaign(conn, campaign_id)
    except controls.ControlError as exc:
        raise problem_for(exc) from exc


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
def pause_campaign(campaign_id: uuid.UUID, body: CampaignPauseIn, user: CampaignAdmin) -> CampaignOut:
    return _campaign_control(
        campaign_id,
        user,
        lambda c: controls.pause_campaign(c, campaign_id=campaign_id, operator_id=user.id, reason=body.reason),
    )


@router.post("/campaigns/{campaign_id}/resume", response_model=CampaignOut)
def resume_campaign(campaign_id: uuid.UUID, user: CampaignAdmin) -> CampaignOut:
    return _campaign_control(
        campaign_id, user, lambda c: controls.resume_campaign(c, campaign_id=campaign_id, operator_id=user.id)
    )


@router.put("/campaigns/{campaign_id}/pilot-limit", response_model=CampaignOut)
def pilot_limit(campaign_id: uuid.UUID, body: PilotLimitIn, user: CampaignAdmin) -> CampaignOut:
    """How many NEW people this campaign may ever email. null = no limit (full volume; audited)."""
    return _campaign_control(
        campaign_id,
        user,
        lambda c: controls.set_pilot_limit(
            c, campaign_id=campaign_id, operator_id=user.id, max_contacts=body.max_contacts
        ),
    )


__all__ = ["mask_email", "router"]


class NewCampaignIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    daily_cap: int = Field(default=50, ge=1, le=1000)
    pilot_max_contacts: int | None = Field(default=100, ge=1, le=controls.PILOT_MAX)


@router.post("/clients/{tenant_id}/campaigns", response_model=CampaignOut, status_code=201)
def create_campaign(tenant_id: uuid.UUID, body: NewCampaignIn, user: CampaignAdmin) -> CampaignOut:
    """A new campaign in draft, targeting the client's configured countries, in Pilot Mode by default."""
    from app.core.audit import write_audit
    from app.domain.tenants.config import load_config

    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        try:
            _, cfg = load_config(conn, tenant_id)
        except LookupError as exc:
            raise ProblemError(409, "Set up the client's offer first") from exc
        cid = conn.execute(
            text("""INSERT INTO campaigns (tenant_id, name, status, target_countries, language, daily_cap,
                    pilot_max_contacts) VALUES (:t, :n, 'draft', :c, :l, :d, :p) RETURNING id"""),
            {
                "t": tenant_id,
                "n": body.name.strip(),
                "c": cfg.icp.countries,
                "l": cfg.language,
                "d": body.daily_cap,
                "p": body.pilot_max_contacts,
            },
        ).scalar_one()
        write_audit(
            conn,
            action="campaign.created",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=tenant_id,
            entity_type="campaign",
            entity_id=cid,
            after={"daily_cap": body.daily_cap, "pilot_max_contacts": body.pilot_max_contacts},
        )
        return _campaign(conn, cid)


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignOut)
def start_campaign(campaign_id: uuid.UUID, user: CampaignAdmin) -> CampaignOut:
    """Draft -> active, only for an active client (onboarding complete). Hunting starts on the next run."""
    from app.core.audit import write_audit

    with transaction(user.db_context(), get_engine()) as conn:
        r = conn.execute(
            text("""SELECT c.tenant_id, c.status, t.status AS tenant_status FROM campaigns c
                    JOIN tenants t ON t.id = c.tenant_id WHERE c.id = :c FOR UPDATE OF c"""),
            {"c": campaign_id},
        ).first()
        if r is None:
            raise ProblemError(404, "Campaign not found")
        if r.tenant_status != "active":
            raise ProblemError(409, "Activate the client first (onboarding checklist)")
        if r.status != "draft":
            raise ProblemError(409, "Only a draft campaign can be started")
        conn.execute(
            text("UPDATE campaigns SET status = 'active', started_at = now() WHERE id = :c"), {"c": campaign_id}
        )
        write_audit(
            conn,
            action="campaign.started",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=r.tenant_id,
            entity_type="campaign",
            entity_id=campaign_id,
        )
        return _campaign(conn, campaign_id)
