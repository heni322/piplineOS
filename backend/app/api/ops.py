"""Operations: readiness probe, Prometheus metrics (bearer token, aggregate counts only, never personal data),
and the deliverability controls (inbox status, operator resume after a kill-switch pause)."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import CurrentUser, require, settings_dep
from app.core.audit import write_audit
from app.core.errors import ProblemError
from app.core.security import Permission
from app.db.session import DbContext, get_engine, transaction
from app.settings import Settings

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
        _gauge(
            lines,
            "pipelineos_inboxes_paused",
            "inboxes paused (kill switch or operator)",
            [({}, one("SELECT count(*) FROM inboxes WHERE status = 'paused'"))],
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


@router.get("/api/v1/inboxes", response_model=list[InboxOut])
def inboxes(user: Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]) -> list[InboxOut]:
    """Read-only health view: the kill switch itself runs in the deliverability task, not here."""
    from app.config_paths import config_root
    from app.domain.deliverability.killswitch import Thresholds

    th = Thresholds.load(config_root())
    now = datetime.now(UTC)
    with transaction(user.db_context(), get_engine()) as conn:
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
            )
        )
    return out


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
