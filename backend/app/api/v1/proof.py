"""Customer proof: testimonials written and consented to by the client's admin, withdrawable at any time.
The operator only reads them (and only those still consented), to use within the scope the client chose."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import CurrentUser, require
from app.core.audit import write_audit
from app.core.errors import ProblemError
from app.core.security import Permission
from app.db.session import get_engine, transaction

router = APIRouter(tags=["proof"])
ClientAdmin = Annotated[CurrentUser, Depends(require(Permission.SIGN_TERMS))]
Viewer = Annotated[CurrentUser, Depends(require(Permission.VIEW_TENANT))]


class TestimonialIn(BaseModel):
    quote: str = Field(min_length=20, max_length=1000)
    attribution: str | None = Field(default=None, max_length=200)
    scope: Literal["anonymous", "named", "named_with_logo"]


class TestimonialOut(BaseModel):
    id: str
    tenant_id: str
    client: str
    quote: str
    attribution: str | None
    scope: str
    consented_at: datetime
    withdrawn_at: datetime | None


_SQL = """SELECT x.*, t.legal_name FROM client_testimonials x JOIN tenants t ON t.id = x.tenant_id"""


def _out(r) -> TestimonialOut:  # type: ignore[no-untyped-def]
    return TestimonialOut(
        id=str(r["id"]),
        tenant_id=str(r["tenant_id"]),
        client=r["legal_name"],
        quote=r["quote"],
        attribution=r["attribution"],
        scope=r["scope"],
        consented_at=r["consented_at"],
        withdrawn_at=r["withdrawn_at"],
    )


@router.get("/testimonials", response_model=list[TestimonialOut])
def list_testimonials(user: Viewer, include_withdrawn: bool = False) -> list[TestimonialOut]:
    """The client sees their own (including withdrawn); the operator sees every client's consented ones."""
    show_withdrawn = include_withdrawn and user.role != "operator"
    with transaction(user.db_context(), get_engine()) as conn:
        rows = conn.execute(
            text(
                _SQL
                + (" WHERE true" if show_withdrawn else " WHERE x.withdrawn_at IS NULL")
                + " ORDER BY x.consented_at DESC"
            )
        ).mappings()
        return [_out(r) for r in rows]


@router.post("/testimonials", response_model=TestimonialOut, status_code=201)
def give_testimonial(body: TestimonialIn, user: ClientAdmin) -> TestimonialOut:
    if user.role != "tenant_admin" or user.tenant_id is None:
        raise ProblemError(403, "Only the client's admin can give a testimonial")
    if body.scope != "anonymous" and len((body.attribution or "").strip()) < 2:
        raise ProblemError(422, "Say how you want to be named")
    with transaction(user.db_context(), get_engine()) as conn:
        tid = conn.execute(
            text("""INSERT INTO client_testimonials (tenant_id, quote, attribution, scope, consented_by)
                    VALUES (:t, :q, :a, :s, :u) RETURNING id"""),
            {
                "t": user.tenant_id,
                "q": body.quote.strip(),
                "a": (body.attribution or "").strip() or None,
                "s": body.scope,
                "u": user.id,
            },
        ).scalar_one()
        write_audit(
            conn,
            action="testimonial.consented",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=user.tenant_id,
            entity_type="testimonial",
            entity_id=tid,
            after={"scope": body.scope},
        )
        return _out(conn.execute(text(_SQL + " WHERE x.id = :i"), {"i": tid}).mappings().one())


@router.post("/testimonials/{testimonial_id}/withdraw", response_model=TestimonialOut)
def withdraw(testimonial_id: uuid.UUID, user: ClientAdmin) -> TestimonialOut:
    """Consent withdrawn: the testimonial is never shown again. Kept (not deleted) as the record of consent."""
    if user.role != "tenant_admin" or user.tenant_id is None:
        raise ProblemError(403, "Only the client's admin can withdraw it")
    with transaction(user.db_context(), get_engine()) as conn:
        done = conn.execute(
            text("""UPDATE client_testimonials SET withdrawn_at = now() WHERE id = :i AND withdrawn_at IS NULL
                    RETURNING id"""),
            {"i": testimonial_id},
        ).first()
        if done is None:
            raise ProblemError(404, "Not found or already withdrawn")
        write_audit(
            conn,
            action="testimonial.withdrawn",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=user.tenant_id,
            entity_type="testimonial",
            entity_id=testimonial_id,
        )
        return _out(conn.execute(text(_SQL + " WHERE x.id = :i"), {"i": testimonial_id}).mappings().one())
