"""Checks page (operator console): run all technical checks on demand, and list the emails that failed or are
stuck, each with its reason in plain words and what to do. The console's alert uses the summary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, require, settings_dep
from app.core.security import Permission
from app.db.session import get_engine, transaction
from app.domain import system_checks
from app.domain.outbound import problems
from app.settings import Settings

router = APIRouter(tags=["checks"])
Operator = Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]


class CheckOut(BaseModel):
    key: str
    title: str
    status: Literal["ok", "warn", "fail", "info"]
    detail: str
    fix: str = ""


class ChecksOut(BaseModel):
    checked_at: datetime
    ok: bool  # no check failed
    checks: list[CheckOut]


class ReasonOut(BaseModel):
    code: str
    text: str
    fix: str


class ProblemOut(BaseModel):
    id: str
    client: str
    campaign: str
    company: str
    status: str
    severity: Literal["error", "warning"]
    what: str
    fix: str
    reasons: list[ReasonOut]
    when: datetime


class ProblemsOut(BaseModel):
    errors_last_7_days: int
    problems: list[ProblemOut]


@router.post("/system-checks", response_model=ChecksOut)
def run_checks(user: Operator, settings: Annotated[Settings, Depends(settings_dep)]) -> ChecksOut:
    """POST: it makes live calls (job queue, worker, the AI provider). Read-only all the same."""
    from datetime import UTC

    engine = get_engine()
    with transaction(user.db_context(), engine) as conn:
        checks = [CheckOut(**c) for c in system_checks.run_all(engine, conn, settings)]
    return ChecksOut(checked_at=datetime.now(UTC), ok=all(c.status != "fail" for c in checks), checks=checks)


@router.get("/sending-problems", response_model=ProblemsOut)
def sending_problems(user: Operator, days: int = 30) -> ProblemsOut:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = problems.sending_problems(conn, days=max(1, min(days, 90)))
        errors = problems.error_count(conn, days=7)
    return ProblemsOut(errors_last_7_days=errors, problems=[ProblemOut(**r) for r in rows])
