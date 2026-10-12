"""Leads: masked by default. Unmasking requires a reason and writes pii_access_log BEFORE data is returned."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import CurrentUser, cipher_dep, require
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.masking import mask_email, mask_name, mask_url
from app.core.security import Permission
from app.db.session import get_engine, transaction

router = APIRouter(prefix="/leads", tags=["leads"])


class LeadOut(BaseModel):
    id: str
    tenant_id: str
    state: str
    state_changed_at: datetime
    company_name: str | None
    prospect_name: str | None
    prospect_email: str | None
    title: str | None
    country_code: str | None
    masked: bool = True


class LeadPage(BaseModel):
    items: list[LeadOut]
    next_cursor: str | None


class UnmaskIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class UnmaskOut(BaseModel):
    lead_id: str
    prospect_name: str | None
    prospect_email: str | None
    profile_url: str | None
    masked: bool = False


def _cursor(ts: datetime, lead_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{lead_id}".encode()).decode()


def _parse_cursor(c: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, lid = base64.urlsafe_b64decode(c.encode()).decode().split("|")
        return datetime.fromisoformat(ts), uuid.UUID(lid)
    except Exception as exc:
        raise ProblemError(400, "Invalid cursor") from exc


@router.get("", response_model=LeadPage)
def list_leads(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_LEADS))],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
    state: str | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> LeadPage:
    params: dict[str, object] = {"lim": limit + 1, "state": state}
    where = ["(CAST(:state AS text) IS NULL OR l.state = :state)"]
    if cursor:
        ts, lid = _parse_cursor(cursor)
        where.append("(l.state_changed_at, l.id) < (:cts, :cid)")
        params |= {"cts": ts, "cid": lid}
    # Built only from the constant fragments above; every value is a bound parameter.
    sql = (
        "SELECT l.id, l.tenant_id, l.state, l.state_changed_at, c.name AS company_name, c.name_is_personal, "  # noqa: S608
        "p.name_enc, p.email_enc, p.title, p.country_code FROM leads l JOIN companies c ON c.id = l.company_id "
        "LEFT JOIN prospects p ON p.id = l.prospect_id WHERE "
        + " AND ".join(where)
        + " ORDER BY l.state_changed_at DESC, l.id DESC LIMIT :lim"
    )
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        items = []
        for r in rows[:limit]:
            name = cipher.decrypt(conn, r["tenant_id"], "prospects.name", r["name_enc"]) if r["name_enc"] else None
            email = cipher.decrypt(conn, r["tenant_id"], "prospects.email", r["email_enc"]) if r["email_enc"] else None
            items.append(
                LeadOut(
                    id=str(r["id"]),
                    tenant_id=str(r["tenant_id"]),
                    state=r["state"],
                    state_changed_at=r["state_changed_at"],
                    company_name="[personal]" if r["name_is_personal"] else r["company_name"],
                    prospect_name=mask_name(name),
                    prospect_email=mask_email(email),
                    title=r["title"],
                    country_code=r["country_code"],
                )
            )
    nxt = _cursor(rows[limit - 1]["state_changed_at"], rows[limit - 1]["id"]) if len(rows) > limit else None
    return LeadPage(items=items, next_cursor=nxt)


@router.post("/{lead_id}/unmask", response_model=UnmaskOut)
def unmask(
    lead_id: uuid.UUID,
    body: UnmaskIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require(Permission.UNMASK_PII))],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> UnmaskOut:
    with transaction(user.db_context(), get_engine()) as conn:
        r = (
            conn.execute(
                text(
                    "SELECT l.tenant_id, p.id AS prospect_id, p.name_enc, p.email_enc, p.profile_url_enc "
                    "FROM leads l LEFT JOIN prospects p ON p.id = l.prospect_id WHERE l.id = :id"
                ),
                {"id": lead_id},
            )
            .mappings()
            .first()
        )
        if r is None:
            raise ProblemError(404, "Lead not found")
        conn.execute(
            text("""INSERT INTO pii_access_log (user_id, tenant_id, lead_id, prospect_id, action, reason, ip_country)
              VALUES (:u, :t, :l, :p, 'unmask', :r, :c)"""),
            {
                "u": user.id,
                "t": r["tenant_id"],
                "l": lead_id,
                "p": r["prospect_id"],
                "r": body.reason,
                "c": request.headers.get("x-client-country"),
            },
        )
        t = r["tenant_id"]
        return UnmaskOut(
            lead_id=str(lead_id),
            prospect_name=cipher.decrypt(conn, t, "prospects.name", r["name_enc"]) if r["name_enc"] else None,
            prospect_email=cipher.decrypt(conn, t, "prospects.email", r["email_enc"]) if r["email_enc"] else None,
            profile_url=cipher.decrypt(conn, t, "prospects.profile_url", r["profile_url_enc"])
            if r["profile_url_enc"]
            else None,
        )


__all__ = ["mask_url", "router"]
