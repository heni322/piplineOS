"""Targeting API: the operator edits who to look for (keywords, SIC codes, countries, watchlist) without touching
qualification criteria or pricing, so what the client signed stays signed."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.tenants import signoffs
from app.domain.tenants.config import load_config
from tests.factories import make_tenant_user

pytestmark = pytest.mark.db


@pytest.fixture
def world(clean_db, settings):  # type: ignore[no-untyped-def]
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


BODY = {
    "industry_keywords": ["managed IT", " service desk ", "managed IT", ""],
    "exclude_keywords": ["school"],
    "hiring_keywords": ["service desk", "-intern"],
    "sic_codes": ["62020", "62090"],
    "countries": ["gb", "FR"],
    "watchlist": [
        {"name": " Contoso Managed IT Ltd ", "company_number": "01234567", "job_board": "greenhouse:contoso",
         "domain": "", "country": None},
        {"name": "Fabrikam SAS", "job_board": "lever-eu:fabrikam", "country": "FR"},
    ],
}  # fmt: skip


def test_targeting_round_trip_keeps_signed_terms(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    c, demo = world
    before = c.get(f"/api/v1/clients/{demo.tenant_id}/targeting")
    assert before.status_code == 200
    b = before.json()
    assert "GB" in b["available_countries"] and "FR" in b["available_countries"]
    assert b["watchlist"] == [] and b["sic_codes"] == []
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        signed_before = signoffs.current_is_signed(conn, demo.tenant_id, "qualification_criteria")

    r = c.put(f"/api/v1/clients/{demo.tenant_id}/targeting", json=BODY)
    assert r.status_code == 200 and r.json()["version"] == b["config_version"] + 1
    after = c.get(f"/api/v1/clients/{demo.tenant_id}/targeting").json()
    assert after["industry_keywords"] == ["managed IT", "service desk"]  # trimmed, de-duplicated, blanks dropped
    assert after["countries"] == ["GB", "FR"] and after["sic_codes"] == ["62020", "62090"]
    assert after["watchlist"][0] == {
        "name": "Contoso Managed IT Ltd",
        "company_number": "01234567",
        "domain": None,
        "country": None,
        "job_board": "greenhouse:contoso",
    }
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        _, cfg = load_config(conn, demo.tenant_id)
        assert cfg.icp.hiring_keywords == ["service desk", "-intern"]
        assert cfg.brand_voice.sender_name  # everything else carried over
        assert signoffs.current_is_signed(conn, demo.tenant_id, "qualification_criteria") == signed_before


def test_invalid_targeting_is_refused_with_a_readable_reason(world) -> None:  # type: ignore[no-untyped-def]
    c, demo = world
    url = f"/api/v1/clients/{demo.tenant_id}/targeting"
    bad_sic = c.put(url, json=BODY | {"sic_codes": ["6202"]})
    assert bad_sic.status_code == 422 and "SIC" in bad_sic.json()["detail"]
    bad_board = c.put(url, json=BODY | {"watchlist": [{"name": "X", "job_board": "workday:x"}]})
    assert bad_board.status_code == 422 and "job_board" in bad_board.json()["detail"]
    assert c.put(url, json=BODY | {"countries": ["XX"]}).status_code == 422
    assert c.put(url, json=BODY | {"countries": []}).status_code == 422
    assert c.put(url, json=BODY | {"industry_keywords": [" "]}).status_code == 422
    assert c.put(url, json=BODY | {"watchlist": [{"name": "  "}]}).status_code == 422


def test_only_operators_can_read_or_change_targeting(world, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    c, demo = world
    make_tenant_user(clean_db, settings, cipher, demo.tenant_id, email="admin@northwind.example")
    t = TestClient(c.app)
    r = t.post("/api/v1/auth/login", json={"email": "admin@northwind.example", "password": "tenant-password-123"})
    assert r.status_code == 200
    t.headers["x-csrf-token"] = t.cookies["pos_csrf"]
    url = f"/api/v1/clients/{demo.tenant_id}/targeting"
    assert t.get(url).status_code == 403 and t.put(url, json=BODY).status_code == 403
    assert TestClient(c.app).get(url).status_code == 401
