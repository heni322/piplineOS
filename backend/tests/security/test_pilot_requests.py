"""Inbound pilot requests: anyone can ask to be contacted (consent required, honeypot, rate limit); only the
operator can read them, decrypted; clients never see them; nothing is stored in clear text."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from tests.factories import make_operator, make_tenant, make_tenant_user
from tests.security.test_api import SECRET, login

pytestmark = pytest.mark.db
REQ = {
    "name": "Sam Carter",
    "company": "Acme IT Ltd",
    "email": "Sam@Acme-IT.example",
    "message": "We'd like a pilot in November.",
    "consent": True,
}


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    app = create_app()
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    t = make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, t, email="admin@client.example")
    return {
        "anon": TestClient(app),
        "op": login(TestClient(app), "op@pipelineos.example", "operator-password-123", pyotp.TOTP(SECRET).now()),
        "client": login(TestClient(app), "admin@client.example", "tenant-password-123"),
    }


def test_a_company_can_ask_for_a_pilot_and_only_the_operator_reads_it(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    assert world["anon"].post("/api/v1/pilot-requests", json=REQ | {"consent": False}).status_code == 422
    assert world["anon"].post("/api/v1/pilot-requests", json=REQ).status_code == 202
    assert world["anon"].post("/api/v1/pilot-requests", json=REQ | {"website": "spam.example"}).status_code == 202
    rows = world["op"].get("/api/v1/pilot-requests").json()
    assert len(rows) == 1  # the honeypot one was dropped
    assert rows[0]["email"] == "sam@acme-it.example" and rows[0]["name"] == "Sam Carter" and rows[0]["status"] == "new"
    assert world["client"].get("/api/v1/pilot-requests").status_code == 403
    assert world["anon"].get("/api/v1/pilot-requests").status_code == 401
    assert (
        world["op"].post(f"/api/v1/pilot-requests/{rows[0]['id']}/status", json={"status": "contacted"}).status_code
        == 204
    )
    with transaction(DbContext(role="operator"), clean_db) as conn:
        raw = conn.execute(text("SELECT name_enc, email_enc, status FROM pilot_requests")).one()
    assert b"sam@acme" not in bytes(raw.email_enc) and b"Sam Carter" not in bytes(raw.name_enc)  # encrypted at rest
    assert raw.status == "contacted"
    with transaction(DbContext(), clean_db) as conn:  # an anonymous database context can't read them back
        assert conn.execute(text("SELECT count(*) FROM pilot_requests")).scalar() == 0


def test_submissions_are_rate_limited(world) -> None:  # type: ignore[no-untyped-def]
    codes = [world["anon"].post("/api/v1/pilot-requests", json=REQ).status_code for _ in range(7)]
    assert codes[:5] == [202] * 5 and 429 in codes[5:]
