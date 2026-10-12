"""Compliance log, go-live checklist (operator sign-off only), adequacy expiry status."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import CurrentUser, cipher_dep, require
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.security import Permission, verify_totp
from app.db.session import get_engine, transaction

router = APIRouter(tags=["governance"])


class DecisionOut(BaseModel):
    id: str
    lead_id: str
    stage: str
    outcome: str
    reason_codes: list[str]
    rules_applied: dict[str, str]
    created_at: datetime


@router.get("/compliance/decisions", response_model=list[DecisionOut])
def decisions(
    lead_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_COMPLIANCE_LOG))],
    tenant_id: uuid.UUID | None = None,
) -> list[DecisionOut]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(
                text("SELECT * FROM compliance_decisions WHERE lead_id = :l ORDER BY created_at DESC LIMIT 200"),
                {"l": lead_id},
            )
            .mappings()
            .all()
        )
    return [
        DecisionOut(
            id=str(r["id"]),
            lead_id=str(r["lead_id"]),
            stage=r["stage"],
            outcome=r["outcome"],
            reason_codes=r["reason_codes"],
            rules_applied=r["rules_applied"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


class ChecklistOut(BaseModel):
    id: str
    key: str
    scope: str
    title: str
    description: str
    signed_off: bool
    signed_off_at: datetime | None


class SignOffIn(BaseModel):
    totp_code: str = Field(min_length=6, max_length=10)  # MFA re-prompt for every sign-off
    evidence_url: str | None = Field(default=None, max_length=1000)


@router.get("/golive", response_model=list[ChecklistOut])
def checklist(user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))]) -> list[ChecklistOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = conn.execute(text("SELECT * FROM golive_checklist_items ORDER BY scope, key")).mappings().all()
    return [
        ChecklistOut(
            id=str(r["id"]),
            key=r["key"],
            scope=r["scope"],
            title=r["title"],
            description=r["description"],
            signed_off=r["signed_off_at"] is not None,
            signed_off_at=r["signed_off_at"],
        )
        for r in rows
    ]


@router.post("/golive/{item_id}/sign-off", status_code=204)
def sign_off(
    item_id: uuid.UUID,
    body: SignOffIn,
    user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> None:
    with transaction(user.db_context(), get_engine()) as conn:
        enc = conn.execute(text("SELECT mfa_secret_enc FROM users WHERE id = :id"), {"id": user.id}).scalar()
        if not enc or not verify_totp(cipher.decrypt(conn, None, "users.mfa_secret", enc), body.totp_code):
            raise ProblemError(403, "MFA verification failed")
        conn.execute(text("SELECT golive_sign_off(:i, :e)"), {"i": item_id, "e": body.evidence_url})


class AdequacyOut(BaseModel):
    key: str
    valid_until: date | None
    days_remaining: int | None
    warning: bool


def adequacy_status(today: date, valid_until: date | None) -> tuple[int | None, bool]:
    if valid_until is None:
        return None, True
    remaining = (valid_until - today).days
    return remaining, remaining <= 365


@router.get("/governance/adequacy", response_model=list[AdequacyOut])
def adequacy(user: Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]) -> list[AdequacyOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = (
            conn.execute(text("SELECT key, valid_until FROM current_transfer_mechanisms WHERE kind = 'eu_uk_adequacy'"))
            .mappings()
            .all()
        )
    today = datetime.now(UTC).date()
    out = []
    for r in rows:
        remaining, warn = adequacy_status(today, r["valid_until"])
        out.append(AdequacyOut(key=r["key"], valid_until=r["valid_until"], days_remaining=remaining, warning=warn))
    return out
