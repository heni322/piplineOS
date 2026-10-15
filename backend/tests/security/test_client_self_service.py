"""Client self-service: the client admin manages their own target accounts (companies only) and sees their setup
progress. Viewers can read but not change; nothing they accepted is invalidated; one client never touches another."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.dataset import DEMO_CONFIG
from app.domain.tenants import signoffs
from app.domain.tenants.config import load_config, save_config
from tests.factories import make_tenant, make_tenant_user
from tests.security.test_api import login

pytestmark = pytest.mark.db


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    a, b = make_tenant(clean_db), make_tenant(clean_db)
    uid, _ = make_tenant_user(clean_db, settings, cipher, a, email="admin@a.example")
    make_tenant_user(clean_db, settings, cipher, a, email="viewer@a.example", role="tenant_viewer")
    make_tenant_user(clean_db, settings, cipher, b, email="admin@b.example")
    with transaction(DbContext(role="operator"), clean_db) as conn:
        for t in (a, b):
            save_config(conn, t, DEMO_CONFIG, None)
    with transaction(DbContext(tenant_id=a, role="tenant_admin", user_id=uid), clean_db) as conn:
        signoffs.sign(conn, tenant_id=a, kind="qualification_criteria", user_id=uid, method="portal")
    app = create_app()
    return {
        "a": a,
        "b": b,
        "admin": login(TestClient(app), "admin@a.example", "tenant-password-123"),
        "viewer": login(TestClient(app), "viewer@a.example", "tenant-password-123"),
        "other": login(TestClient(app), "admin@b.example", "tenant-password-123"),
    }


def test_client_manages_their_own_target_accounts(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    admin = world["admin"]
    rows = [
        {"name": " Contoso IT Ltd ", "company_number": "01234567", "job_board": "greenhouse:contoso"},
        {"name": "Fabrikam Ltd", "domain": "fabrikam.example"},
        {"name": "  "},
    ]
    r = admin.put("/api/v1/account/watchlist", json={"watchlist": rows})
    assert r.status_code == 200, r.text
    assert [w["name"] for w in r.json()["watchlist"]] == ["Contoso IT Ltd", "Fabrikam Ltd"]  # blank row dropped
    assert world["viewer"].get("/api/v1/account/watchlist").json()["watchlist"][0]["company_number"] == "01234567"
    assert world["other"].get("/api/v1/account/watchlist").json()["watchlist"] == []  # the other client's own list
    with transaction(DbContext.system(world["b"]), clean_db) as conn:
        assert load_config(conn, world["b"])[1].watchlist == []
    # accepted criteria stay accepted: only the watchlist changed
    with transaction(DbContext.system(world["a"]), clean_db) as conn:
        assert signoffs.current_is_signed(conn, world["a"], "qualification_criteria")
        assert (
            conn.execute(text("SELECT after->>'by' FROM audit_log WHERE action = 'tenant.watchlist_set'")).scalar()
            == "client"
        )


def test_viewers_cannot_change_and_bad_rows_are_explained(world) -> None:  # type: ignore[no-untyped-def]
    assert world["viewer"].put("/api/v1/account/watchlist", json={"watchlist": []}).status_code == 403
    bad = world["admin"].put(
        "/api/v1/account/watchlist", json={"watchlist": [{"name": "X Ltd", "job_board": "myspace:x"}]}
    )
    assert bad.status_code == 422 and "row 1" in bad.json()["detail"]
    too_many = world["admin"].put(
        "/api/v1/account/watchlist", json={"watchlist": [{"name": f"C{i}"} for i in range(501)]}
    )
    assert too_many.status_code == 422


def test_client_sees_setup_progress(world) -> None:  # type: ignore[no-untyped-def]
    p = world["admin"].get("/api/v1/account/setup-progress").json()
    assert p["total"] >= 8 and 0 < p["done"] < p["total"]
    assert {"key": "criteria_signed", "done": True} in [{"key": i["key"], "done": i["done"]} for i in p["items"]]


def test_a_save_on_an_old_version_is_refused_and_row_numbers_match_the_screen(world) -> None:  # type: ignore[no-untyped-def]
    admin = world["admin"]
    v = admin.get("/api/v1/account/watchlist").json()["config_version"]
    assert (
        admin.put("/api/v1/account/watchlist", json={"watchlist": [{"name": "A Ltd"}], "config_version": v}).status_code
        == 200
    )
    stale = admin.put("/api/v1/account/watchlist", json={"watchlist": [{"name": "B Ltd"}], "config_version": v})
    assert stale.status_code == 409  # someone saved since this copy was opened: nothing is overwritten
    bad = admin.put(
        "/api/v1/account/watchlist",
        json={"watchlist": [{"name": " "}, {"name": "A Ltd"}, {"name": "B Ltd", "job_board": "myspace:x"}]},
    )
    assert bad.status_code == 422 and "row 3" in bad.json()["detail"]  # blank rows still count as rows


def test_each_admin_sets_only_their_own_email_preferences(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    assert world["admin"].get("/api/v1/account/preferences").json() == {"weekly_report": True}
    assert world["admin"].put("/api/v1/account/preferences", json={"weekly_report": False}).status_code == 200
    assert world["admin"].get("/api/v1/account/preferences").json() == {"weekly_report": False}
    assert world["other"].get("/api/v1/account/preferences").json() == {"weekly_report": True}  # untouched
    assert world["viewer"].put("/api/v1/account/preferences", json={"weekly_report": False}).status_code == 403
