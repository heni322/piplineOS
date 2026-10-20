"""Operations: readiness probe, Prometheus metrics (bearer token, aggregate counts only, never personal data),
and the deliverability controls (inbox status, operator resume after a kill-switch pause)."""

from __future__ import annotations

import hmac
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import CurrentUser, require, settings_dep
from app.core.audit import write_audit
from app.core.errors import ProblemError
from app.core.security import Permission
from app.db.session import DbContext, get_engine, transaction
from app.domain.tenants.controls import CONTACTED_STATUSES
from app.settings import Settings

if TYPE_CHECKING:
    from app.domain.deliverability.dns_check import Lookup

router = APIRouter(tags=["ops"])


@router.get("/api/v1/ready", include_in_schema=False)
def ready(settings: Annotated[Settings, Depends(settings_dep)]) -> JSONResponse:
    """Ready = database reachable with the expected schema; Valkey reachable (workers and limits need it)."""
    checks: dict[str, str] = {}
    try:
        with transaction(DbContext.system(), get_engine()) as conn:
            conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            checks["schema"] = rev or "missing"
    except Exception:  # report, don't leak details
        checks.setdefault("database", "unreachable")
        checks.setdefault("schema", "missing")
    try:
        import redis

        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5).ping()
        checks["valkey"] = "ok"
    except Exception:
        checks["valkey"] = "unreachable"
    ok = checks.get("database") == "ok" and checks.get("valkey") == "ok" and checks.get("schema") != "missing"
    return JSONResponse({"ready": ok, **checks}, status_code=200 if ok else 503)


def _gauge(lines: list[str], name: str, help_: str, values: list[tuple[dict[str, str], float]]) -> None:
    lines += [f"# HELP {name} {help_}", f"# TYPE {name} gauge"]
    for labels, v in values:
        lab = ",".join(f'{k}="{str(val).replace(chr(34), "")}"' for k, val in sorted(labels.items()))
        lines.append(f"{name}{{{lab}}} {v}" if lab else f"{name} {v}")


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request, settings: Annotated[Settings, Depends(settings_dep)]) -> PlainTextResponse:
    expected = settings.metrics_token.get_secret_value() if settings.metrics_token else None
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise ProblemError(404, "Not found")  # disabled or wrong token: indistinguishable
    now = datetime.now(UTC)
    lines: list[str] = []
    with transaction(DbContext(role="operator"), get_engine()) as conn:

        def one(sql: str, **p: object) -> float:
            return float(conn.execute(text(sql), p).scalar() or 0)

        def grouped(sql: str, key: str, **p: object) -> list[tuple[dict[str, str], float]]:
            return [({key: str(r[0])}, float(r[1])) for r in conn.execute(text(sql), p)]

        _gauge(lines, "pipelineos_global_go_live", "1 if GLOBAL_GO_LIVE is on", [({}, float(settings.global_go_live))])
        _gauge(
            lines,
            "pipelineos_golive_complete",
            "1 if every go-live item is signed off",
            [({}, one("SELECT golive_complete('uk')::int"))],
        )
        _gauge(
            lines,
            "pipelineos_live_tenants",
            "clients in live mode",
            [({}, one("SELECT count(*) FROM tenants WHERE live_mode"))],
        )
        _gauge(
            lines,
            "pipelineos_pending_approvals",
            "messages waiting for review",
            [({}, one("SELECT count(*) FROM messages WHERE status = 'pending_approval'"))],
        )
        _gauge(
            lines,
            "pipelineos_open_escalations",
            "open escalations by reason",
            grouped("SELECT reason, count(*) FROM escalations WHERE status <> 'resolved' GROUP BY reason", "reason"),
        )
        _gauge(
            lines,
            "pipelineos_dpr_open",
            "open data-protection requests",
            [
                (
                    {},
                    one(
                        "SELECT count(*) FROM data_protection_requests "
                        "WHERE status NOT IN ('completed','refused_with_reason')"
                    ),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_dpr_due_7d",
            "open data-protection requests due within 7 days",
            [
                (
                    {},
                    one(
                        """SELECT count(*) FROM data_protection_requests
               WHERE status NOT IN ('completed','refused_with_reason')
               AND deadline_at < :d""",
                        d=now + timedelta(days=7),
                    ),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_dpr_overdue",
            "overdue data-protection requests",
            [
                (
                    {},
                    one(
                        """SELECT count(*) FROM data_protection_requests
               WHERE status NOT IN ('completed','refused_with_reason')
               AND deadline_at < :d""",
                        d=now,
                    ),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_reply_sla_breaches",
            "replies unclassified after 5 minutes",
            [
                (
                    {},
                    one(
                        "SELECT count(*) FROM replies WHERE classified_at IS NULL AND received_at < :d",
                        d=now - timedelta(minutes=5),
                    ),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_sends_last_hour",
            "messages sent in the last hour by mode",
            grouped(
                """SELECT coalesce(mode_at_send, 'none'), count(*) FROM messages WHERE sent_at > :d
               GROUP BY mode_at_send""",
                "mode",
                d=now - timedelta(hours=1),
            ),
        )
        _gauge(
            lines,
            "pipelineos_needs_reconciliation",
            "sends with an unknown provider outcome",
            [({}, one("SELECT count(*) FROM messages WHERE status = 'needs_reconciliation'"))],
        )
        _gauge(
            lines,
            "pipelineos_outbox_backlog",
            "undispatched outbox events",
            [({}, one("SELECT count(*) FROM outbox WHERE dispatched_at IS NULL"))],
        )
        _gauge(
            lines,
            "pipelineos_outbox_oldest_seconds",
            "age of the oldest undispatched event",
            [
                (
                    {},
                    one(
                        "SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0) FROM outbox "
                        "WHERE dispatched_at IS NULL"
                    ),
                )
            ],
        )
        from app.domain.sources.health import summary as source_summary

        health = source_summary(conn)
        _gauge(
            lines,
            "pipelineos_source_failing",
            "1 when a signal source's last run failed and it has not recovered (see the Go-live page)",
            [({"source": h.source}, 1.0 if h.status == "failing" else 0.0) for h in health],
        )
        _gauge(
            lines,
            "pipelineos_source_silent",
            "1 when a signal source answered successfully but found nothing at all for 7 days (retired or changed?)",
            [({"source": h.source}, 1.0 if h.status == "silent" else 0.0) for h in health],
        )
        _gauge(
            lines,
            "pipelineos_source_failures_24h",
            "failed runs of each signal source in the last 24 hours",
            [({"source": h.source}, float(h.failures_24h)) for h in health],
        )
        _gauge(
            lines,
            "pipelineos_replies_recovered_24h",
            "replies found by the safety sweep because their push notification never arrived (last 24 hours)",
            [
                (
                    {},
                    one(
                        """SELECT coalesce(sum((after->>'count')::int), 0) FROM audit_log
                           WHERE action = 'inbound.sweep_recovered' AND created_at > now() - interval '24 hours'"""
                    ),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_inboxes_paused",
            "inboxes paused (kill switch or operator)",
            [({}, one("SELECT count(*) FROM inboxes WHERE status = 'paused'"))],
        )
        _gauge(
            lines,
            "pipelineos_clients_paused",
            "clients whose sending is stopped, by who stopped it (the client's own emergency stop, or the operator)",
            [
                ({"by": who}, one(f"SELECT count(*) FROM tenants WHERE status = 'paused' AND {cond}"))  # noqa: S608
                for who, cond in (
                    ("client", "paused_reason LIKE 'Client: %'"),
                    ("operator", "coalesce(paused_reason, '') NOT LIKE 'Client: %'"),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_campaigns_pilot_full",
            "active campaigns whose pilot contact limit is reached (new first emails are held)",
            [
                (
                    {},
                    one(
                        """SELECT count(*) FROM campaigns c WHERE c.status = 'active'
                           AND c.pilot_max_contacts IS NOT NULL
                           AND (SELECT count(DISTINCT m.lead_id) FROM messages m WHERE m.campaign_id = c.id
                                AND m.kind = 'outreach' AND m.sequence_step = 0 AND m.status = ANY(:contacted))
                               >= c.pilot_max_contacts""",
                        contacted=list(CONTACTED_STATUSES),
                    ),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_pilot_requests_waiting",
            "pilot requests from the landing page unanswered for more than an hour",
            [
                (
                    {},
                    one(
                        "SELECT count(*) FROM pilot_requests WHERE status = 'new' "
                        "AND created_at < now() - interval '1 hour'"
                    ),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_invoices_overdue",
            "issued invoices past their due date with money still owed",
            [
                (
                    {},
                    one(
                        """SELECT count(*) FROM invoices i WHERE i.status = 'issued' AND i.due_date < current_date
                           AND i.total_minor > coalesce((SELECT sum(p.amount_minor) FROM invoice_payments p
                                                         WHERE p.invoice_id = i.id), 0)"""
                    ),
                )
            ],
        )
        _gauge(
            lines,
            "pipelineos_llm_calls_last_hour",
            "AI calls in the last hour by outcome",
            grouped(
                "SELECT outcome, count(*) FROM llm_calls WHERE created_at > :d GROUP BY outcome",
                "outcome",
                d=now - timedelta(hours=1),
            ),
        )
        _gauge(
            lines,
            "pipelineos_invoices_blocked",
            "draft invoices that cannot be issued yet",
            [({}, one("SELECT count(*) FROM invoices WHERE status = 'draft' AND cardinality(blocked_reasons) > 0"))],
        )
        from app.domain.billing.service import vat_threshold_status

        vat = vat_threshold_status(conn, now.date())
        _gauge(
            lines,
            "pipelineos_vat_threshold_pct",
            "rolling 12-month turnover as % of the VAT threshold",
            [({}, float(vat["pct"] or 0))],
        )
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


class InboxOut(BaseModel):
    id: str
    tenant_id: str
    tenant_name: str
    address: str
    status: str
    provider: str
    daily_cap: int
    warmup_day: int
    sent_today: int
    window_days: int
    live_sent: int
    hard_bounces: int
    complaints: int
    hard_bounce_rate: float | None
    complaint_rate: float | None
    max_hard_bounce_rate: float
    max_complaint_rate: float
    min_sent: int
    last_paused_at: datetime | None
    last_pause_reason: list[str]
    placement_checked_at: datetime | None = None
    placement_passed: bool | None = None
    placement_current: bool = False  # a pass recorded within the last 30 days
    placement_reasons: list[str] = []


@router.get("/api/v1/inboxes", response_model=list[InboxOut])
def inboxes(user: Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]) -> list[InboxOut]:
    """Read-only health view: the kill switch itself runs in the deliverability task, not here."""
    from app.config_paths import config_root
    from app.domain.deliverability.killswitch import Thresholds
    from app.domain.deliverability.placement import latest_by_inbox

    th = Thresholds.load(config_root())
    now = datetime.now(UTC)
    with transaction(user.db_context(), get_engine()) as conn:
        placement = latest_by_inbox(conn)
        rows = (
            conn.execute(
                text("""SELECT i.*, t.legal_name,
                (SELECT count(*) FROM messages m WHERE m.inbox_id = i.id AND m.sent_at >= date_trunc('day', :now)
                   AND m.status IN ('sent','dry_run_sent','bounced','complained')) AS sent_today,
                (SELECT count(*) FROM messages m WHERE m.inbox_id = i.id AND m.sent_at >= :since
                   AND m.mode_at_send = 'live' AND m.status IN ('sent','bounced','complained')) AS live_sent,
                (SELECT count(*) FROM messages m WHERE m.inbox_id = i.id AND m.sent_at >= :since
                   AND m.mode_at_send = 'live' AND m.status = 'bounced' AND m.last_error LIKE 'hard_bounce%') AS hard,
                (SELECT count(*) FROM messages m WHERE m.inbox_id = i.id AND m.sent_at >= :since
                   AND m.mode_at_send = 'live' AND m.status = 'complained') AS complained,
                (SELECT a.created_at FROM audit_log a WHERE a.entity_type = 'inbox' AND a.entity_id = i.id::text
                   AND a.action IN ('deliverability.inbox_paused','deliverability.inbox_paused_manually')
                   ORDER BY a.created_at DESC LIMIT 1) AS paused_at,
                (SELECT a.after FROM audit_log a WHERE a.entity_type = 'inbox' AND a.entity_id = i.id::text
                   AND a.action IN ('deliverability.inbox_paused','deliverability.inbox_paused_manually')
                   ORDER BY a.created_at DESC LIMIT 1) AS paused_after
              FROM inboxes i JOIN tenants t ON t.id = i.tenant_id ORDER BY t.legal_name, i.address"""),
                {"now": now, "since": now - timedelta(days=th.window_days)},
            )
            .mappings()
            .all()
        )
    out = []
    for r in rows:
        sent = int(r["live_sent"])
        after = r["paused_after"] or {}
        reasons = after.get("reasons") or ([after["reason"]] if after.get("reason") else [])
        out.append(
            InboxOut(
                id=str(r["id"]),
                tenant_id=str(r["tenant_id"]),
                tenant_name=r["legal_name"],
                address=r["address"],
                status=r["status"],
                provider=r["provider"],
                daily_cap=r["daily_cap"],
                warmup_day=r["warmup_day"],
                sent_today=int(r["sent_today"]),
                window_days=th.window_days,
                live_sent=sent,
                hard_bounces=int(r["hard"]),
                complaints=int(r["complained"]),
                hard_bounce_rate=round(int(r["hard"]) / sent, 4) if sent else None,
                complaint_rate=round(int(r["complained"]) / sent, 4) if sent else None,
                max_hard_bounce_rate=th.max_hard_bounce_rate,
                max_complaint_rate=th.max_complaint_rate,
                min_sent=th.min_sent,
                last_paused_at=r["paused_at"] if r["status"] == "paused" else None,
                last_pause_reason=[str(x) for x in reasons] if r["status"] == "paused" else [],
                placement_checked_at=(pc := placement.get(r["id"])) and pc.checked_at,
                placement_passed=pc.passed if pc else None,
                placement_current=bool(pc and pc.current(now)),
                placement_reasons=pc.reasons if pc else [],
            )
        )
    return out


class PlacementIn(BaseModel):
    score: float | None = Field(default=None, ge=0, le=10)
    spf_pass: bool
    dkim_pass: bool
    dmarc_pass: bool
    blacklisted: bool
    provider_results: dict[
        Annotated[str, Field(min_length=2, max_length=40)], Literal["inbox", "spam", "promotions", "missing"]
    ] = Field(max_length=10)
    evidence_url: str | None = Field(default=None, max_length=500, pattern=r"^https://")


class PlacementOut(BaseModel):
    passed: bool
    reasons: list[str]


@router.post("/api/v1/inboxes/{inbox_id}/placement-checks", response_model=PlacementOut, status_code=201)
def placement_check(
    inbox_id: uuid.UUID,
    body: PlacementIn,
    user: Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))],
) -> PlacementOut:
    """Record the result of the go-live runbook's placement check (C2) for one inbox. Pass/fail is decided here by
    the runbook's rule, not by the person recording it. Nothing is sent."""
    from app.domain.deliverability.placement import PlacementCheck, record

    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    with transaction(user.db_context(), get_engine()) as conn:
        tenant = conn.execute(text("SELECT tenant_id FROM inboxes WHERE id = :i"), {"i": inbox_id}).scalar()
        if tenant is None:
            raise ProblemError(404, "Inbox not found")
        passed, reasons = record(
            conn, tenant_id=tenant, inbox_id=inbox_id, check=PlacementCheck(**body.model_dump()), operator_id=user.id
        )
    return PlacementOut(passed=passed, reasons=reasons)


class PauseIn(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)


@router.post("/api/v1/inboxes/{inbox_id}/pause", status_code=204)
def pause(
    inbox_id: uuid.UUID, body: PauseIn, user: Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))]
) -> None:
    """Manual pause: nothing more is sent from this inbox until it is resumed with a written reason."""
    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    with transaction(user.db_context(), get_engine()) as conn:
        t = conn.execute(
            text("UPDATE inboxes SET status = 'paused' WHERE id = :i AND status <> 'paused' RETURNING tenant_id"),
            {"i": inbox_id},
        ).scalar()
        if t is None:
            raise ProblemError(409, "Inbox is already paused or doesn't exist")
        write_audit(
            conn,
            action="deliverability.inbox_paused_manually",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=t,
            entity_type="inbox",
            entity_id=inbox_id,
            after={"reason": body.reason},
        )


class ResumeIn(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)  # what was fixed (list cleaned, DNS corrected, ...)


@router.post("/api/v1/inboxes/{inbox_id}/resume", status_code=204)
def resume(
    inbox_id: uuid.UUID, body: ResumeIn, user: Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))]
) -> None:
    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    with transaction(user.db_context(), get_engine()) as conn:
        t = conn.execute(
            text("UPDATE inboxes SET status = 'active' WHERE id = :i AND status = 'paused' RETURNING tenant_id"),
            {"i": inbox_id},
        ).scalar()
        if t is None:
            raise ProblemError(409, "Inbox is not paused")
        write_audit(
            conn,
            action="deliverability.inbox_resumed",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=t,
            entity_type="inbox",
            entity_id=inbox_id,
            after={"reason": body.reason},
        )


# ------------------------------------------------------------------------------------------- sending domains
class SendingDomainOut(BaseModel):
    id: str
    tenant_id: str
    tenant_name: str
    domain: str
    status: str
    spf_ok: bool
    dkim_ok: bool
    dmarc_ok: bool
    dmarc_policy: str | None
    issues: list[str]
    checked_at: datetime | None
    providers: list[str]


def _dns_lookup() -> Lookup:  # replaced in tests (no network)
    from app.domain.deliverability.dns_check import dns_lookup

    return dns_lookup()


_DOMAINS_SQL = """SELECT d.*, t.legal_name, coalesce(array_agg(DISTINCT i.provider)
                    FILTER (WHERE i.provider IS NOT NULL), '{}') AS providers
                  FROM sending_domains d JOIN tenants t ON t.id = d.tenant_id
                  LEFT JOIN inboxes i ON i.sending_domain_id = d.id"""


def _domain_out(r) -> SendingDomainOut:  # type: ignore[no-untyped-def]
    return SendingDomainOut(
        id=str(r["id"]),
        tenant_id=str(r["tenant_id"]),
        tenant_name=r["legal_name"],
        domain=r["domain"],
        status=r["status"],
        spf_ok=r["spf_ok"],
        dkim_ok=r["dkim_ok"],
        dmarc_ok=r["dmarc_ok"],
        dmarc_policy=r["dmarc_policy"],
        issues=[str(x) for x in (r["dns_issues"] or [])],
        checked_at=r["dns_checked_at"],
        providers=sorted(r["providers"]),
    )


@router.get("/api/v1/sending-domains", response_model=list[SendingDomainOut])
def sending_domains(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))],
) -> list[SendingDomainOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = conn.execute(text(_DOMAINS_SQL + " GROUP BY d.id, t.legal_name ORDER BY t.legal_name, d.domain"))
        return [_domain_out(r) for r in rows.mappings()]


@router.post("/api/v1/sending-domains/{domain_id}/check", response_model=SendingDomainOut)
def check_sending_domain(
    domain_id: uuid.UUID, user: Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))]
) -> SendingDomainOut:
    """Reads the domain's public DNS (SPF, DKIM, DMARC) now and stores the result. Changes nothing in DNS."""
    from app.domain.deliverability.dns_check import check_and_store

    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    with transaction(user.db_context(), get_engine()) as conn:
        if conn.execute(text("SELECT 1 FROM sending_domains WHERE id = :d"), {"d": domain_id}).first() is None:
            raise ProblemError(404, "Not found")
        check_and_store(conn, domain_id, _dns_lookup(), datetime.now(UTC))
        r = conn.execute(text(_DOMAINS_SQL + " WHERE d.id = :d GROUP BY d.id, t.legal_name"), {"d": domain_id})
        return _domain_out(r.mappings().one())


# ------------------------------------------------------------------ onboarding: sending domain and inbox
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


class DomainIn(BaseModel):
    domain: str = Field(min_length=4, max_length=253)


@router.post("/api/v1/clients/{tenant_id}/sending-domains", response_model=SendingDomainOut, status_code=201)
def add_sending_domain(
    tenant_id: uuid.UUID, body: DomainIn, user: Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))]
) -> SendingDomainOut:
    """A separate domain the client sends from (never their main domain). DNS is checked afterwards (Check DNS)."""
    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    domain = body.domain.strip().lower().rstrip(".")
    if not _DOMAIN_RE.match(domain):
        raise ProblemError(422, "Not a valid domain name")
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        if conn.execute(text("SELECT 1 FROM tenants WHERE id = :t"), {"t": tenant_id}).first() is None:
            raise ProblemError(404, "Client not found")
        if conn.execute(text("SELECT 1 FROM sending_domains WHERE domain = :d"), {"d": domain}).first():
            raise ProblemError(409, "This domain is already registered")
        did = conn.execute(
            text("INSERT INTO sending_domains (tenant_id, domain) VALUES (:t, :d) RETURNING id"),
            {"t": tenant_id, "d": domain},
        ).scalar_one()
        write_audit(
            conn,
            action="sending_domain.added",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=tenant_id,
            entity_type="sending_domain",
            entity_id=did,
            after={"domain": domain},
        )
        r = conn.execute(text(_DOMAINS_SQL + " WHERE d.id = :d GROUP BY d.id, t.legal_name"), {"d": did})
        return _domain_out(r.mappings().one())


class NewInboxIn(BaseModel):
    sending_domain_id: uuid.UUID
    address: str = Field(min_length=6, max_length=254)
    display_name: str = Field(min_length=2, max_length=100)
    provider: Literal["msgraph", "google"]


class NewInboxOut(BaseModel):
    id: str
    address: str
    provider: str
    status: str
    daily_cap: int


@router.post("/api/v1/clients/{tenant_id}/inboxes", response_model=NewInboxOut, status_code=201)
def add_inbox(
    tenant_id: uuid.UUID,
    body: NewInboxIn,
    user: Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))],
) -> NewInboxOut:
    """A mailbox on the sending domain. It starts warming at the warm-up start cap (config/warmup.yaml)."""
    from app.config_paths import config_root
    from app.domain.deliverability.warmup import WarmupPlan

    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    address = body.address.strip().lower()
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        dom = conn.execute(
            text("SELECT domain FROM sending_domains WHERE id = :d AND tenant_id = :t"),
            {"d": body.sending_domain_id, "t": tenant_id},
        ).scalar()
        if dom is None:
            raise ProblemError(404, "Sending domain not found for this client")
        local, _, host = address.partition("@")
        if not local or host != dom or not re.match(r"^[a-z0-9._%+-]{1,64}$", local):
            raise ProblemError(422, f"The address must be on {dom}")
        if conn.execute(text("SELECT 1 FROM inboxes WHERE address = :a"), {"a": address}).first():
            raise ProblemError(409, "This inbox already exists")
        cap = WarmupPlan.load(config_root()).start_cap
        iid = conn.execute(
            text("""INSERT INTO inboxes (tenant_id, sending_domain_id, address, display_name, provider, status,
                    daily_cap) VALUES (:t, :d, :a, :n, :p, 'warming', :c) RETURNING id"""),
            {
                "t": tenant_id,
                "d": body.sending_domain_id,
                "a": address,
                "n": body.display_name.strip(),
                "p": body.provider,
                "c": cap,
            },
        ).scalar_one()
        write_audit(
            conn,
            action="inbox.added",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=tenant_id,
            entity_type="inbox",
            entity_id=iid,
            after={"provider": body.provider, "daily_cap": cap},
        )
    return NewInboxOut(id=str(iid), address=address, provider=body.provider, status="warming", daily_cap=cap)
