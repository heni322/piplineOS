"""Inbox placement checks (go-live runbook C2, readiness blocker #17): recorded by an operator, judged by the
runbook's rule (not by whoever records it), append-only, invisible to client accounts, and shown on the readiness
list. Nothing is sent."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.deliverability.placement import PlacementCheck, evaluate
from tests.security.test_golive_prep import _login

pytestmark = pytest.mark.db
GOOD = {
    "score": 9.6,
    "spf_pass": True,
    "dkim_pass": True,
    "dmarc_pass": True,
    "blacklisted": False,
    "provider_results": {"gmail": "inbox", "outlook.com": "inbox", "m365": "inbox"},
    "evidence_url": "https://www.mail-tester.com/test-abc",
}


def test_the_runbook_rule() -> None:
    ok = PlacementCheck(**GOOD)  # type: ignore[arg-type]
    assert evaluate(ok) == (True, [])
    bad = PlacementCheck(**(GOOD | {"score": 7.5, "dkim_pass": False, "provider_results": {"gmail": "spam"}}))  # type: ignore[arg-type]
    passed, reasons = evaluate(bad)
    assert not passed and len(reasons) == 3 and any("gmail: landed in spam" in r for r in reasons)
    assert not evaluate(PlacementCheck(**(GOOD | {"provider_results": {}})))[0]  # type: ignore[arg-type]
    assert evaluate(PlacementCheck(**(GOOD | {"score": None})))[0]  # type: ignore[arg-type]  # score optional


def test_recording_reading_and_readiness(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    with transaction(DbContext(role="operator"), clean_db) as conn:
        inbox = conn.execute(text("SELECT id FROM inboxes LIMIT 1")).scalar_one()
        conn.execute(text("UPDATE tenants SET status = 'active'"))
    app = create_app()
    op = _login(
        TestClient(app),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )

    def item() -> dict:  # type: ignore[type-arg]
        return next(i for i in op.get("/api/v1/governance/readiness").json() if i["key"] == "placement")

    assert item()["state"] == "todo"
    r = op.post(f"/api/v1/inboxes/{inbox}/placement-checks", json=GOOD | {"provider_results": {"gmail": "spam"}})
    assert r.status_code == 201 and r.json()["passed"] is False
    row = next(i for i in op.get("/api/v1/inboxes").json() if i["id"] == str(inbox))
    assert row["placement_passed"] is False and not row["placement_current"]
    assert item()["state"] == "todo"

    assert op.post(f"/api/v1/inboxes/{inbox}/placement-checks", json=GOOD).json() == {"passed": True, "reasons": []}
    row = next(i for i in op.get("/api/v1/inboxes").json() if i["id"] == str(inbox))
    assert row["placement_current"] and row["placement_checked_at"]
    assert item()["state"] == "done"

    bad_link = GOOD | {"evidence_url": "javascript:alert(1)"}
    assert op.post(f"/api/v1/inboxes/{inbox}/placement-checks", json=bad_link).status_code == 422
    assert (
        op.post(
            f"/api/v1/inboxes/{inbox}/placement-checks".replace(str(inbox), str(demo.tenant_id)), json=GOOD
        ).status_code
        == 404
    )

    client = _login(TestClient(app), demo.client_email, demo.client_password or "")
    assert client.post(f"/api/v1/inboxes/{inbox}/placement-checks", json=GOOD).status_code == 403

    with transaction(DbContext(role="operator"), clean_db) as conn, pytest.raises(DBAPIError):
        conn.execute(text("UPDATE inbox_placement_checks SET passed = true"))  # append-only
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM audit_log WHERE action = 'deliverability.placement_checked'")
            ).scalar()
            == 2
        )
