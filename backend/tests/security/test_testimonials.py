"""Customer proof: only the client's admin gives or withdraws a testimonial; a withdrawn one is never listed for the
operator; one client never sees another's; nothing can be deleted."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import DbContext, transaction
from tests.factories import make_operator, make_tenant, make_tenant_user
from tests.security.test_api import SECRET, login

pytestmark = pytest.mark.db
QUOTE = "Three qualified meetings in the first month, all with companies we had never reached before."


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    app = create_app()
    a, b = make_tenant(clean_db), make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, a, email="admin@a.example")
    make_tenant_user(clean_db, settings, cipher, a, email="viewer@a.example", role="tenant_viewer")
    make_tenant_user(clean_db, settings, cipher, b, email="admin@b.example")
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    c = lambda e, pw="tenant-password-123", totp=None: login(TestClient(app), e, pw, totp)  # noqa: E731
    return {
        "a": c("admin@a.example"),
        "viewer": c("viewer@a.example"),
        "b": c("admin@b.example"),
        "op": c("op@pipelineos.example", "operator-password-123", pyotp.TOTP(SECRET).now()),
    }


def test_client_gives_and_withdraws_consent(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    a, op = world["a"], world["op"]
    assert a.post("/api/v1/testimonials", json={"quote": QUOTE, "scope": "named"}).status_code == 422  # no name
    r = a.post("/api/v1/testimonials", json={"quote": QUOTE, "scope": "named", "attribution": "Sam, MD"})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert [x["id"] for x in op.get("/api/v1/testimonials").json()] == [tid]
    assert world["b"].get("/api/v1/testimonials").json() == []  # another client sees nothing
    assert world["viewer"].post("/api/v1/testimonials", json={"quote": QUOTE, "scope": "anonymous"}).status_code == 403
    assert world["b"].post(f"/api/v1/testimonials/{tid}/withdraw").status_code == 404  # not theirs
    assert op.post(f"/api/v1/testimonials/{tid}/withdraw").status_code == 403  # only the client withdraws
    assert a.post(f"/api/v1/testimonials/{tid}/withdraw").json()["withdrawn_at"] is not None
    assert op.get("/api/v1/testimonials").json() == []  # never shown again
    assert len(a.get("/api/v1/testimonials", params={"include_withdrawn": True}).json()) == 1  # their record
    with pytest.raises(DBAPIError), transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(text("DELETE FROM client_testimonials"))
