"""Client invites and password resets: single-use, expiring, hashed tokens; no enumeration; emails are dry-run."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo

pytestmark = pytest.mark.db


@pytest.fixture
def app_client(clean_db, settings):  # type: ignore[no-untyped-def]
    import pyotp

    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    c = TestClient(create_app())
    r = c.post(
        "/api/v1/auth/login",
        json={
            "email": "op@pipelineos.example",
            "password": demo.operator_password,
            "totp_code": pyotp.TOTP(demo.operator_totp_secret or "").now(),
        },
    )
    assert r.status_code == 200
    c.headers["x-csrf-token"] = c.cookies["pos_csrf"]
    return c, demo


def _token(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


def test_invite_is_emailed_dry_run_and_the_link_works_once(app_client, clean_db, owner_engine) -> None:  # type: ignore[no-untyped-def]
    op, demo = app_client
    r = op.post(f"/api/v1/clients/{demo.tenant_id}/users", json={"email": "new.admin@northwind.example"})
    assert r.status_code == 201
    token = _token(r.json()["dev_invite_link"])
    with transaction(DbContext(role="operator"), clean_db) as conn:
        rows = conn.execute(text("SELECT token_hash, purpose, used_at FROM account_tokens")).all()
        assert len(rows) == 1 and rows[0].purpose == "invite" and rows[0].used_at is None
        assert token.encode() not in bytes(rows[0].token_hash)  # only the hash is stored
        assert (
            conn.execute(text("SELECT count(*) FROM dry_run_log WHERE idempotency_key LIKE 'system:invite:%'")).scalar()
            == 1
        )
    anon = TestClient(op.app)
    assert (
        anon.post(
            "/api/v1/auth/login", json={"email": "new.admin@northwind.example", "password": "anything-at-all"}
        ).status_code
        == 401
    )
    assert anon.post("/api/v1/auth/set-password", json={"token": token, "password": "short"}).status_code == 422
    assert (
        anon.post(
            "/api/v1/auth/set-password", json={"token": token, "password": "a-long-enough-pass-phrase"}
        ).status_code
        == 204
    )
    assert (
        anon.post(
            "/api/v1/auth/set-password", json={"token": token, "password": "another-long-pass-phrase"}
        ).status_code
        == 400
    )
    ok = anon.post(
        "/api/v1/auth/login", json={"email": "new.admin@northwind.example", "password": "a-long-enough-pass-phrase"}
    )
    assert ok.status_code == 200 and ok.json()["role"] == "tenant_admin"


def test_expired_and_forged_links_are_refused(app_client, clean_db, owner_engine) -> None:  # type: ignore[no-untyped-def]
    op, demo = app_client
    token = _token(
        op.post(f"/api/v1/clients/{demo.tenant_id}/users", json={"email": "late@northwind.example"}).json()[
            "dev_invite_link"
        ]
    )
    with owner_engine.begin() as c:
        c.execute(text("UPDATE account_tokens SET expires_at = now() - interval '1 minute'"))
    anon = TestClient(op.app)
    assert (
        anon.post(
            "/api/v1/auth/set-password", json={"token": token, "password": "a-long-enough-pass-phrase"}
        ).status_code
        == 400
    )
    assert (
        anon.post(
            "/api/v1/auth/set-password", json={"token": "x" * 43, "password": "a-long-enough-pass-phrase"}
        ).status_code
        == 400
    )


def test_password_reset_has_no_enumeration_and_is_rate_limited(app_client, clean_db) -> None:  # type: ignore[no-untyped-def]
    op, demo = app_client
    anon = TestClient(op.app)
    assert anon.post("/api/v1/auth/forgot-password", json={"email": "nobody@nowhere.example"}).status_code == 204
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM account_tokens")).scalar() == 0
    for _ in range(4):  # one account gets at most 3 reset emails an hour
        assert anon.post("/api/v1/auth/forgot-password", json={"email": demo.client_email}).status_code == 204
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM account_tokens WHERE purpose = 'reset'")).scalar() == 3
        assert (
            conn.execute(text("SELECT count(*) FROM dry_run_log WHERE idempotency_key LIKE 'system:reset:%'")).scalar()
            == 3
        )
    statuses = [anon.post("/api/v1/auth/forgot-password", json={"email": "x@y.example"}).status_code for _ in range(5)]
    assert 429 in statuses  # per-IP limit on top


def test_reset_signs_out_existing_sessions(app_client, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    op, demo = app_client
    client = TestClient(op.app)
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": demo.client_email, "password": demo.client_password}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me").status_code == 200
    from app.domain.tenants import accounts

    url = accounts.request_reset(clean_db, settings, cipher, email=demo.client_email)
    assert url is not None
    assert (
        TestClient(op.app)
        .post("/api/v1/auth/set-password", json={"token": _token(url), "password": "brand-new-pass-phrase"})
        .status_code
        == 204
    )
    assert client.get("/api/v1/auth/me").status_code == 401
