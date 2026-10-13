"""The client's own CRM. The client's admin connects it from the portal (so the access token never travels by email
or chat); the operator can see the status and, during onboarding, connect it on the client's behalf.
The token is write-only: no endpoint ever returns it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, cipher_dep, require, settings_dep
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.security import Permission
from app.db.session import get_engine, transaction
from app.domain.outbound import crm
from app.settings import Settings

router = APIRouter(tags=["crm"])
Manager = Annotated[CurrentUser, Depends(require(Permission.MANAGE_INTEGRATIONS))]
Viewer = Annotated[CurrentUser, Depends(require(Permission.VIEW_TENANT))]
Operator = Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]


class StageMap(BaseModel):
    booked: str = Field(min_length=1, max_length=100)
    confirmed: str = Field(min_length=1, max_length=100)
    attended: str = Field(min_length=1, max_length=100)
    no_show: str = Field(min_length=1, max_length=100)
    cancelled: str = Field(min_length=1, max_length=100)


class ConnectIn(BaseModel):
    provider: Literal["hubspot"] = "hubspot"
    token: str = Field(min_length=10, max_length=500)
    pipeline_id: str = Field(min_length=1, max_length=100)
    stages: StageMap


class DisconnectIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class ConnectionOut(BaseModel):
    connected: bool
    provider: str | None = None
    pipeline_id: str | None = None
    stages: dict[str, str] | None = None
    status: str | None = None
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    disconnected_reason: str | None = None
    meetings_synced: int = 0
    meetings_waiting: int = 0
    meetings_failed: int = 0


def _tenant(user: CurrentUser, tenant_id: uuid.UUID | None) -> uuid.UUID:
    if user.role == "operator":
        if tenant_id is None:
            raise ProblemError(422, "Choose a client", "tenant_id is required for operators")
        return tenant_id
    if user.tenant_id is None:
        raise ProblemError(403, "Forbidden")
    return user.tenant_id  # a client user is always pinned to their own organisation


def _out(status: dict[str, Any] | None) -> ConnectionOut:
    if status is None:
        return ConnectionOut(connected=False)
    links = status["links"]
    return ConnectionOut(
        connected=status["status"] == "connected",
        provider=status["provider"],
        pipeline_id=status["pipeline_id"],
        stages=status["stage_map"],
        status=status["status"],
        connected_at=status["updated_at"],
        last_sync_at=status["last_sync_at"],
        disconnected_reason=status["disconnected_reason"],
        meetings_synced=links.get("ok", 0) + links.get("skipped", 0),
        meetings_waiting=links.get("pending", 0) + links.get("creating", 0),
        meetings_failed=links.get("error", 0),
    )


@router.get("/crm/connection", response_model=ConnectionOut)
def get_connection(user: Viewer, tenant_id: uuid.UUID | None = None) -> ConnectionOut:
    t = _tenant(user, tenant_id)
    with transaction(user.db_context(t), get_engine()) as conn:
        return _out(crm.connection_status(conn, t))


@router.put("/crm/connection", response_model=ConnectionOut)
def put_connection(
    body: ConnectIn,
    user: Manager,
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
    tenant_id: uuid.UUID | None = None,
) -> ConnectionOut:
    t = _tenant(user, tenant_id)
    with transaction(user.db_context(t), get_engine()) as conn:
        try:
            crm.connect(
                conn,
                cipher,
                tenant_id=t,
                provider=body.provider,
                token=body.token,
                pipeline_id=body.pipeline_id,
                stage_map=body.stages.model_dump(),
                user_id=user.id,
            )
        except crm.CrmConfigError as exc:
            raise ProblemError(422, "Check the CRM details", str(exc)) from exc
        return _out(crm.connection_status(conn, t))


@router.post("/crm/connection/disconnect", response_model=ConnectionOut)
def post_disconnect(body: DisconnectIn, user: Manager, tenant_id: uuid.UUID | None = None) -> ConnectionOut:
    t = _tenant(user, tenant_id)
    with transaction(user.db_context(t), get_engine()) as conn:
        if not crm.disconnect(conn, tenant_id=t, user_id=user.id, reason=body.reason):
            raise ProblemError(409, "Not connected", "There is no connected CRM to disconnect")
        return _out(crm.connection_status(conn, t))


@router.post("/crm/sync/run")
def run_sync(
    user: Operator,
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    tenant_id: uuid.UUID,
) -> dict[str, object]:
    """Sync now (the worker also does this every few minutes). Dry-run unless all three live keys are on."""
    r = crm.sync_tenant(get_engine(), settings, cipher, tenant_id, now=datetime.now(UTC))
    return r.__dict__
