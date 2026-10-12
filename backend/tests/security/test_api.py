"""API security: authentication, MFA, CSRF, tenant isolation over HTTP, masking + logged unmask, unsubscribe."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from tests.factories import make_lead, make_operator, make_tenant, make_tenant_user

pytestmark = pytest.mark.db
SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture
def client(clean_db):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    return TestClient(create_app())


def login(c: TestClient, email: str, password: str, totp: str | None = None) -> TestClient:
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password, "totp_code": totp})
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = c.cookies["pos_csrf"]
    return c


def test_health_and_security_headers(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["global_go_live"] == "False"
    for h in ("x-content-type-options", "x-frame-options", "content-security-policy", "x-correlation-id"):
        assert h in r.headers
    assert r.headers["x-robots-tag"] == "noindex, nofollow"


def test_errors_use_problem_json(client: TestClient) -> None:
    r = client.get("/api/v1/leads")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status", "correlation_id"} <= set(r.json())


def test_operator_login_requires_totp(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    r = client.post("/api/v1/auth/login", json={"email": "op@pipelineos.example", "password": "operator-password-123"})
    assert r.status_code == 401
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": "op@pipelineos.example",
            "password": "operator-password-123",
            "totp_code": pyotp.TOTP(SECRET).now(),
        },
    )
    assert r.status_code == 200 and r.json()["mfa_verified"] is True
    assert "pos_session" in r.cookies


def test_login_failures_are_generic_and_lock_the_account(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, t, email="u@client.example")
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@client.example", "password": "x"})
    wrong = client.post("/api/v1/auth/login", json={"email": "u@client.example", "password": "wrong-password"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["title"] == wrong.json()["title"] == "Invalid credentials"
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "u@client.example", "password": "wrong-password"})
    locked = client.post("/api/v1/auth/login", json={"email": "u@client.example", "password": "tenant-password-123"})
    assert locked.status_code == 401  # correct password, but account is locked


def test_csrf_required_for_state_changes(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, t, email="u@client.example")
    login(client, "u@client.example", "tenant-password-123")
    lead = make_lead(clean_db, settings, cipher, t)
    del client.headers["x-csrf-token"]
    r = client.post(f"/api/v1/leads/{lead.lead_id}/unmask", json={"reason": "support call"})
    assert r.status_code == 403 and "CSRF" in r.json()["title"]


def test_tenant_user_sees_only_own_leads_masked(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    a, b = make_tenant(clean_db), make_tenant(clean_db)
    la = make_lead(clean_db, settings, cipher, a, email="alex.morgan@contoso.example")
    lb = make_lead(clean_db, settings, cipher, b)
    make_tenant_user(clean_db, settings, cipher, a, email="viewer@client.example", role="tenant_viewer")
    login(client, "viewer@client.example", "tenant-password-123")
    r = client.get("/api/v1/leads")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["id"] for i in items] == [str(la.lead_id)]
    assert items[0]["prospect_email"] == "a***@c***.example" and items[0]["masked"] is True
    assert "alex.morgan" not in r.text
    # trying to reach tenant B through the operator-only tenant_id parameter does nothing
    r = client.get(f"/api/v1/leads?tenant_id={b}")
    assert str(lb.lead_id) not in r.text
    # viewers cannot unmask
    assert client.post(f"/api/v1/leads/{la.lead_id}/unmask", json={"reason": "curious"}).status_code == 403


def test_unmask_is_logged_before_data_is_returned(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    lead = make_lead(clean_db, settings, cipher, t, email="riley@tailspin.example")
    uid, _ = make_tenant_user(clean_db, settings, cipher, t, email="admin@client.example")
    login(client, "admin@client.example", "tenant-password-123")
    r = client.post(
        f"/api/v1/leads/{lead.lead_id}/unmask",
        json={"reason": "prospect called back"},
        headers={"x-client-country": "TN"},
    )
    assert r.status_code == 200 and r.json()["prospect_email"] == "riley@tailspin.example"
    with transaction(DbContext(role="operator"), clean_db) as conn:
        row = conn.execute(text("SELECT user_id, lead_id, action, reason, ip_country FROM pii_access_log")).one()
        assert (row.user_id, row.lead_id, row.action, row.ip_country) == (uid, lead.lead_id, "unmask", "TN")
    assert client.post(f"/api/v1/leads/{lead.lead_id}/unmask", json={"reason": ""}).status_code == 422


def test_tenant_admin_cannot_unmask_other_tenants_lead(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    a, b = make_tenant(clean_db), make_tenant(clean_db)
    lb = make_lead(clean_db, settings, cipher, b)
    make_tenant_user(clean_db, settings, cipher, a, email="admin@a.example")
    login(client, "admin@a.example", "tenant-password-123")
    assert client.post(f"/api/v1/leads/{lb.lead_id}/unmask", json={"reason": "snooping"}).status_code == 404


def test_tenant_cannot_reach_operator_endpoints(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, t, email="admin@client.example")
    login(client, "admin@client.example", "tenant-password-123")
    assert client.get("/api/v1/golive").status_code == 403
    assert client.get("/api/v1/governance/adequacy").status_code == 403


def test_golive_sign_off_needs_fresh_totp(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    login(client, "op@pipelineos.example", "operator-password-123", pyotp.TOTP(SECRET).now())
    items = client.get("/api/v1/golive").json()
    assert len(items) == 5 and not any(i["signed_off"] for i in items)
    assert client.post(f"/api/v1/golive/{items[0]['id']}/sign-off", json={"totp_code": "000000"}).status_code == 403
    r = client.post(f"/api/v1/golive/{items[0]['id']}/sign-off", json={"totp_code": pyotp.TOTP(SECRET).now()})
    assert r.status_code == 204
    adequacy = client.get("/api/v1/governance/adequacy").json()
    assert adequacy[0]["valid_until"] == "2031-12-27" and adequacy[0]["warning"] is False


def test_logout_revokes_session(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, t, email="u@client.example")
    login(client, "u@client.example", "tenant-password-123")
    token = client.cookies["pos_session"]
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.set("pos_session", token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_one_click_unsubscribe_flow(client: TestClient, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    from app.demo.traces import run_traces
    from tests.conftest import CONFIG

    run_traces(clean_db, settings, CONFIG)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        token_id, tenant_id, lead_id = conn.execute(
            text("SELECT id, tenant_id, lead_id FROM unsubscribe_tokens LIMIT 1")
        ).one()
    from app.domain.suppression import unsubscribe

    token = unsubscribe.sign(settings.key("unsubscribe_signing_key"), token_id)

    # GET only shows a confirmation page (mail scanners prefetch links)
    r = client.get(f"/u/{token}")
    assert r.status_code == 200 and "<form" in r.text and "noindex" in r.text
    with transaction(DbContext.system(tenant_id), clean_db) as conn:
        assert (
            conn.execute(text("SELECT used_at FROM unsubscribe_tokens WHERE id = :i"), {"i": token_id}).scalar() is None
        )

    # tampered tokens are rejected and logged
    bad = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert client.post(f"/u/{bad}").status_code == 400

    # POST (RFC 8058 one-click) suppresses globally and ends the lead; repeat is harmless
    assert client.post(f"/u/{token}", data={"List-Unsubscribe": "One-Click"}).status_code == 200
    assert client.post(f"/u/{token}").status_code == 200
    with transaction(DbContext.system(tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead_id}).scalar() == "objected"
        assert (
            conn.execute(
                text("SELECT count(*) FROM suppression_list WHERE reason = 'unsubscribe' AND scope = 'global'")
            ).scalar()
            >= 1
        )
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'unsubscribe.invalid_token'")).scalar()
            == 1
        )
