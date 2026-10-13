"""Phase 6.1: governance status, countries (counsel review + MFA before enabling), campaigns, inbox health and manual
pause, config tables write-protected in the database, single-use login codes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo

pytestmark = pytest.mark.db


def _login(app, email: str, password: str, code: str | None = None) -> TestClient:  # type: ignore[no-untyped-def]
    c = TestClient(app)
    body = {"email": email, "password": password} | ({"totp_code": code} if code else {})
    r = c.post("/api/v1/auth/login", json=body)
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = c.cookies["pos_csrf"]
    return c


@pytest.fixture
def world(clean_db, settings):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.api.deps import settings_dep
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    app = create_app()
    app.dependency_overrides[settings_dep] = lambda: settings
    totp = pyotp.TOTP(demo.operator_totp_secret or "")
    op = _login(app, "op@pipelineos.example", demo.operator_password or "", totp.now())
    return app, op, demo, totp


def test_governance_status_shows_all_three_keys_off(world) -> None:  # type: ignore[no-untyped-def]
    _, op, _, _ = world
    s = op.get("/api/v1/governance/status").json()
    assert s["global_go_live"] is False and s["golive_complete"] is False
    assert s["golive_signed"] == 0 and s["golive_items"] > 0
    assert s["enabled_countries"] == ["GB"]  # the demo database only
    assert s["live_clients"] == 0
    assert set(s["adapter_modes"].values()) == {"mock"}


def test_country_needs_counsel_review_then_mfa_to_enable(world) -> None:  # type: ignore[no-untyped-def]
    _, op, _, totp = world
    ie = next(c for c in op.get("/api/v1/countries").json() if c["code"] == "IE")
    assert ie["enabled"] is False and ie["can_enable"] is False
    assert ie["blocked_by"] == ["RULE_NOT_VERIFIED_BY_COUNSEL"]
    r = op.post("/api/v1/countries/IE/enable", json={"totp_code": totp.now()})
    assert r.status_code == 409 and "RULE_NOT_VERIFIED_BY_COUNSEL" in r.json()["detail"]

    today = datetime.now(UTC).date()
    review = {"solicitor": "A. Solicitor, Example LLP", "reviewed_on": today.isoformat()}
    assert op.post("/api/v1/countries/IE/counsel-verification", json=review | {"totp_code": "000000"}).status_code == 403
    future = review | {"reviewed_on": (today + timedelta(days=2)).isoformat(), "totp_code": totp.now()}
    assert op.post("/api/v1/countries/IE/counsel-verification", json=future).status_code == 422
    r = op.post("/api/v1/countries/IE/counsel-verification", json=review | {"totp_code": totp.now()})
    assert r.status_code == 201
    again = op.post("/api/v1/countries/IE/counsel-verification", json=review | {"totp_code": totp.now()})
    assert again.status_code == 409

    ie = next(c for c in op.get("/api/v1/countries").json() if c["code"] == "IE")
    assert ie["verified_by_counsel"] is True and ie["can_enable"] is True
    assert "reviewed by A. Solicitor, Example LLP" in ie["source"]
    assert op.post("/api/v1/countries/IE/enable", json={"totp_code": "000000"}).status_code == 403
    assert op.post("/api/v1/countries/IE/enable", json={"totp_code": totp.now()}).status_code == 204
    assert "IE" in op.get("/api/v1/governance/status").json()["enabled_countries"]

    assert op.post("/api/v1/countries/IE/disable", json={"reason": "short"}).status_code == 422
    r = op.post("/api/v1/countries/IE/disable", json={"reason": "Solicitor asked us to pause Ireland"})
    assert r.status_code == 204
    assert "IE" not in op.get("/api/v1/governance/status").json()["enabled_countries"]


def test_restricted_country_stays_blocked_even_after_review(world) -> None:  # type: ignore[no-untyped-def]
    _, op, _, totp = world
    review = {"solicitor": "A. Solicitor", "reviewed_on": datetime.now(UTC).date().isoformat(), "totp_code": totp.now()}
    assert op.post("/api/v1/countries/DE/counsel-verification", json=review).status_code == 201
    de = next(c for c in op.get("/api/v1/countries").json() if c["code"] == "DE")
    assert de["blocked_by"] == ["COUNTRY_NOT_IN_ROLLOUT"] and de["can_enable"] is False
    assert op.post("/api/v1/countries/DE/enable", json={"totp_code": totp.now()}).status_code == 409


def test_editing_a_rule_in_config_drops_the_counsel_review(world, clean_db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import shutil

    from app.config_paths import config_root
    from app.config_sync import sync_all

    _, op, _, totp = world
    review = {"solicitor": "A. Solicitor", "reviewed_on": datetime.now(UTC).date().isoformat(), "totp_code": totp.now()}
    assert op.post("/api/v1/countries/IE/counsel-verification", json=review).status_code == 201
    cfg = tmp_path / "config"
    shutil.copytree(config_root(), cfg)
    ie = cfg / "countries" / "IE.yaml"
    ie.write_text(ie.read_text().replace("restricted_review", "consent_required", 1))
    with transaction(DbContext.system(), clean_db) as conn:
        sync_all(conn, cfg)
    ie_row = next(c for c in op.get("/api/v1/countries").json() if c["code"] == "IE")
    assert ie_row["rule_mode"] == "consent_required" and ie_row["verified_by_counsel"] is False


def test_clients_cannot_reach_governance_or_operations(world) -> None:  # type: ignore[no-untyped-def]
    app, _, demo, _ = world
    cl = _login(app, demo.client_email or "", demo.client_password or "")
    for path in ("/api/v1/governance/status", "/api/v1/countries", "/api/v1/campaigns", "/api/v1/inboxes"):
        assert cl.get(path).status_code == 403, path
    assert cl.post("/api/v1/countries/IE/disable", json={"reason": "a client trying this"}).status_code == 403
    inbox = "00000000-0000-0000-0000-000000000000"
    assert cl.post(f"/api/v1/inboxes/{inbox}/pause", json={"reason": "a client trying this"}).status_code == 403


def test_config_tables_refuse_writes_from_a_client_context(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    _, _, demo, _ = world
    client_ctx = DbContext(tenant_id=demo.tenant_id, role="tenant_admin", user_id=demo.operator_id)
    attempts = [
        """INSERT INTO compliance_rules (country_code, version, content_hash, mode, regime, verified_by_counsel)
           SELECT country_code, version + 100, content_hash, 'permitted_with_opt_out', regime, false
           FROM current_compliance_rules WHERE country_code = 'IE'""",
        """INSERT INTO country_rollout_status (country_code, version, content_hash, name, stage)
           SELECT country_code, version + 100, content_hash, name, stage FROM current_country_rollout
           WHERE country_code = 'IE'""",
        """INSERT INTO tax_rules (code, version, content_hash, applies_when, rate_bps)
           SELECT code, version + 100, content_hash, applies_when, 0 FROM tax_rules LIMIT 1""",
        "INSERT INTO invoice_counters (prefix, year) VALUES ('INV', 2099)",
    ]
    for sql in attempts:
        with pytest.raises(DBAPIError, match="row-level security"), transaction(client_ctx, clean_db) as conn:
            conn.execute(text(sql))
    with transaction(client_ctx, clean_db) as conn:  # reading is still allowed
        assert conn.execute(text("SELECT count(*) FROM current_compliance_rules")).scalar()
        assert conn.execute(text("UPDATE invoice_counters SET last_number = last_number + 1")).rowcount == 0


def test_campaign_list_for_the_run_controls(world) -> None:  # type: ignore[no-untyped-def]
    _, op, demo, _ = world
    rows = op.get("/api/v1/campaigns").json()
    assert [r["id"] for r in rows] == [str(demo.campaign_id)]
    assert rows[0]["tenant_name"] == "Northwind Demo Services Ltd" and "GB" in rows[0]["target_countries"]


def test_inbox_health_and_manual_pause(world) -> None:  # type: ignore[no-untyped-def]
    _, op, _, _ = world
    box = op.get("/api/v1/inboxes").json()[0]
    assert box["live_sent"] == 0 and box["hard_bounce_rate"] is None  # dry-run sends never count as live
    assert box["max_hard_bounce_rate"] == 0.03 and box["min_sent"] == 50
    assert op.post(f"/api/v1/inboxes/{box['id']}/pause", json={"reason": "short"}).status_code == 422
    reason = "Checking DNS after a provider change"
    assert op.post(f"/api/v1/inboxes/{box['id']}/pause", json={"reason": reason}).status_code == 204
    paused = op.get("/api/v1/inboxes").json()[0]
    assert paused["status"] == "paused" and paused["last_pause_reason"] == [reason]
    assert op.post(f"/api/v1/inboxes/{box['id']}/pause", json={"reason": reason}).status_code == 409
    r = op.post(f"/api/v1/inboxes/{box['id']}/resume", json={"reason": "DNS verified, resuming sends"})
    assert r.status_code == 204
    assert op.get("/api/v1/inboxes").json()[0]["last_pause_reason"] == []


def test_a_login_code_works_only_once(world) -> None:  # type: ignore[no-untyped-def]
    app, op, demo, totp = world
    used = totp.now()
    op.post("/api/v1/auth/logout")
    replay = TestClient(app).post(
        "/api/v1/auth/login",
        json={"email": "op@pipelineos.example", "password": demo.operator_password, "totp_code": used},
    )
    assert replay.status_code == 401
    nxt = totp.at(datetime.now(UTC) + timedelta(seconds=30))
    _login(app, "op@pipelineos.example", demo.operator_password or "", nxt)
