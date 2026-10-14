"""Source health for the operator: each signal source's recent runs, failures and last success."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, require
from app.core.security import Permission
from app.db.session import get_engine, transaction
from app.domain.sources.health import summary

router = APIRouter(tags=["sources"])
Operator = Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]


class SourceHealthOut(BaseModel):
    source: str
    status: str
    runs_24h: int
    failures_24h: int
    fetched_24h: int
    last_run_at: datetime
    last_outcome: str
    last_error: str | None
    last_success_at: datetime | None


@router.get("/sources/health", response_model=list[SourceHealthOut])
def source_health(user: Operator) -> list[SourceHealthOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        return [SourceHealthOut(**h.__dict__) for h in summary(conn)]
