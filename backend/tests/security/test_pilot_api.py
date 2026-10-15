"""Emergency stop over HTTP: who may pause and resume what. A client admin can stop their own account (never another
client's, never resume it); viewers cannot; the operator controls clients, campaigns and pilot limits."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from tests.factories import make_lead, make_operator, make_tenant, make_tenant_user
from tests.security.test_api import SECRET, login

pytestmark = pytest.mark.db


@pytest.fixture
def client(clean_db):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    return TestClient(create_app())


def _status(engine, t):  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), engine) as conn:
        return conn.execute(text("SELECT status, paused_reason FROM tenants WHERE id = :t"), {"t": t}).one()


def test_operator_pauses_and_resumes_a_client(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    t = make_tenant(clean_db)
    login(client, "op@pipelineos.example", "operator-password-123", pyotp.TOTP(SECRET).now())
    assert client.post(f"/api/v1/clients/{t}/pause", json={"reason": ""}).status_code == 422
    r = client.post(f"/api/v1/clients/{t}/pause", json={"reason": "complaint received"})
    assert r.status_code == 200 and r.json()["status"] == "paused" and r.json()["paused_reason"] == "complaint received"
    assert client.post(f"/api/v1/clients/{t}/pause", json={"reason": "again"}).status_code == 409
    r = client.post(f"/api/v1/clients/{t}/resume")
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["paused_reason"] is None


def test_operator_campaign_controls(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    login(client, "op@pipelineos.example", "operator-password-123", pyotp.TOTP(SECRET).now())
    c = lead.campaign_id
    listed = next(x for x in client.get("/api/v1/campaigns").json() if x["id"] == str(c))
    assert (listed["pilot_max_contacts"], listed["pilot_contacts_used"]) == (100, 0)
    r = client.put(f"/api/v1/campaigns/{c}/pilot-limit", json={"max_contacts": 250})
    assert r.status_code == 200 and r.json()["pilot_max_contacts"] == 250
    assert client.put(f"/api/v1/campaigns/{c}/pilot-limit", json={"max_contacts": 0}).status_code == 422
    assert (
        client.put(f"/api/v1/campaigns/{c}/pilot-limit", json={"max_contacts": None}).json()["pilot_max_contacts"]
        is None
    )
    r = client.post(f"/api/v1/campaigns/{c}/pause", json={"reason": "review copy"})
    assert r.status_code == 200 and (r.json()["status"], r.json()["paused_reason"]) == ("paused", "review copy")
    assert client.post(f"/api/v1/campaigns/{c}/resume").json()["status"] == "active"
    assert client.post(f"/api/v1/campaigns/{c}/resume").status_code == 409


def test_client_admin_can_stop_only_their_own_account(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    mine, other = make_tenant(clean_db), make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, mine, email="admin@client.example")
    login(client, "admin@client.example", "tenant-password-123")
    assert client.get("/api/v1/account").json()["status"] == "active"
    r = client.post("/api/v1/account/pause", json={"reason": "we got a complaint"})
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert r.json()["paused_reason"] == "Client: we got a complaint"
    assert _status(clean_db, other).status == "active"
    # The client cannot resume, nor reach the operator's controls.
    assert client.post(f"/api/v1/clients/{mine}/resume").status_code == 403
    assert client.post(f"/api/v1/clients/{other}/pause", json={"reason": "xxx"}).status_code == 403
    assert client.post("/api/v1/account/pause", json={"reason": "again"}).status_code == 409
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert (
            conn.execute(
                text("SELECT after->>'by' FROM audit_log WHERE action = 'tenant.paused' AND tenant_id = :t"),
                {"t": mine},
            ).scalar()
            == "client"
        )


def test_viewer_cannot_pause(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, t, role="tenant_viewer", email="v@client.example")
    login(client, "v@client.example", "tenant-password-123")
    assert client.get("/api/v1/account").status_code == 200
    assert client.post("/api/v1/account/pause", json={"reason": "stop"}).status_code == 403
    assert _status(clean_db, t).status == "active"


def test_app_role_cannot_use_the_owner_pause_policy(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    """The narrow policy is for the owner role only: a tenant context on the app role still cannot write tenants."""
    from sqlalchemy.exc import ProgrammingError

    t = make_tenant(clean_db)
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        transaction(DbContext.system(t), clean_db) as conn,
    ):
        conn.execute(text("UPDATE tenants SET status = 'paused' WHERE id = :t"), {"t": t})
    assert _status(clean_db, t).status == "active"
