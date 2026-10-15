"""Inbound pilot requests from the public landing page: a company asks to be contacted about a pilot. This is
consent-based (they wrote to us), so it is the cleanest way to win a first client. Anyone may submit; only the
operator reads. Spam is limited by a per-IP rate limit and a hidden honeypot field."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from app.api.deps import CurrentUser, cipher_dep, require, settings_dep
from app.api.v1.auth import _client_ip, _limit
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.security import Permission
from app.db.session import DbContext, get_engine, transaction
from app.settings import Settings

router = APIRouter(tags=["inbound"])
Operator = Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]
Cipher = Annotated[PiiCipher, Depends(cipher_dep)]
CONSENT = "I agree that PipelineOS may contact me by email about a pilot. I can ask for my details to be deleted."


class PilotRequestIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    company: str = Field(min_length=2, max_length=200)
    email: EmailStr
    message: str | None = Field(default=None, max_length=2000)
    consent: Literal[True]  # the box must be ticked
    website: str | None = Field(default=None, max_length=200)  # honeypot: humans never see or fill it


@router.post("/pilot-requests", status_code=202)
def submit(
    body: PilotRequestIn,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    cipher: Cipher,
) -> Response:
    """Always 202 once validated (a bot filling the honeypot learns nothing)."""
    if not _limit(settings, "pilot:" + _client_ip(request), 0.002, 5):  # about 5 an hour per address
        raise ProblemError(429, "Too many requests")
    if body.website:
        return Response(status_code=202)
    with transaction(DbContext.system(), get_engine()) as conn:
        conn.execute(
            text("""INSERT INTO pilot_requests (company, name_enc, email_enc, message_enc, consent_text)
                    VALUES (:c, :n, :e, :m, :ct)"""),
            {
                "c": body.company.strip(),
                "n": cipher.encrypt(conn, None, "pilot_requests.name", body.name.strip()),
                "e": cipher.encrypt(conn, None, "pilot_requests.email", str(body.email).lower()),
                "m": cipher.encrypt(conn, None, "pilot_requests.message", body.message.strip())
                if body.message and body.message.strip()
                else None,
                "ct": CONSENT,
            },
        )
    return Response(status_code=202)


class PilotRequestOut(BaseModel):
    id: str
    company: str
    name: str
    email: str
    message: str | None
    status: str
    created_at: datetime


@router.get("/pilot-requests", response_model=list[PilotRequestOut])
def list_requests(user: Operator, cipher: Cipher) -> list[PilotRequestOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = conn.execute(text("SELECT * FROM pilot_requests ORDER BY created_at DESC LIMIT 500")).mappings().all()
        return [
            PilotRequestOut(
                id=str(r["id"]),
                company=r["company"],
                name=cipher.decrypt(conn, None, "pilot_requests.name", r["name_enc"]),
                email=cipher.decrypt(conn, None, "pilot_requests.email", r["email_enc"]),
                message=cipher.decrypt(conn, None, "pilot_requests.message", r["message_enc"])
                if r["message_enc"]
                else None,
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]


class StatusIn(BaseModel):
    status: Literal["new", "contacted", "closed"]


@router.post("/pilot-requests/{request_id}/status", status_code=204)
def set_status(request_id: uuid.UUID, body: StatusIn, user: Operator) -> Response:
    with transaction(user.db_context(), get_engine()) as conn:
        done = conn.execute(
            text(
                "UPDATE pilot_requests SET status = :s, handled_by = :u, updated_at = now() WHERE id = :i RETURNING id"
            ),
            {"s": body.status, "u": user.id, "i": request_id},
        ).first()
        if done is None:
            raise ProblemError(404, "Not found")
        write_audit(
            conn,
            action="pilot_request.status",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=None,
            entity_type="pilot_request",
            entity_id=request_id,
            after={"status": body.status},
        )
    return Response(status_code=204)
