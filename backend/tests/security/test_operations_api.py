"""Approval queue + operator API: operator-only, masked by default, logged unmask, idempotent decisions."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.seed import providers, seed_demo
from app.workflows.pipeline import agent_context, run_campaign
from tests.factories import make_tenant_user

pytestmark = pytest.mark.db


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    run_campaign(agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id), providers())
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


def test_queue_shows_everything_needed_and_masks_names(world) -> None:  # type: ignore[no-untyped-def]
    c, _ = world
    r = c.get("/api/v1/approvals")
    assert r.status_code == 200
    page = r.json()
    assert page["total_pending"] == 5 and len(page["items"]) == 5
    item = next(i for i in page["items"] if i["company"].startswith("Contoso"))
    assert item["prospect_name"] == "A*** M***" and item["masked"] is True
    assert "Alex" not in r.text and "alex.morgan" not in r.text  # first name masked inside the body too
    assert len(item["subject_options"]) == 3 and len(item["sequence"]) == 3
    assert item["claims"] and item["claims"][0]["source_url"].startswith("https://jobs.example/")
    assert item["compliance"]["outcome"] == "allow" and item["compliance"]["rules_applied"]["GB"].endswith("@v1")
    assert item["quality_score"] >= 8 and "Northwind Demo Services Ltd" in item["footer_preview"]


def test_unmask_for_editing_is_logged(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    c, _demo = world
    mid = next(
        i["message_id"] for i in c.get("/api/v1/approvals").json()["items"] if i["company"].startswith("Contoso")
    )
    r = c.get(f"/api/v1/approvals/{mid}", params={"unmask_reason": "editing greeting"})
    assert r.status_code == 200 and r.json()["prospect_name"] == "Alex Morgan" and "Hi Alex" in r.text
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert (
            conn.execute(text("SELECT reason FROM pii_access_log WHERE action = 'unmask'")).scalar()
            == "editing greeting"
        )


def test_decision_requires_idempotency_key_and_replays(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    c, demo = world
    mid = c.get("/api/v1/approvals").json()["items"][0]["message_id"]
    assert c.post(f"/api/v1/approvals/{mid}/decision", json={"decision": "approve"}).status_code == 400
    h = {"Idempotency-Key": "k-1"}
    r1 = c.post(f"/api/v1/approvals/{mid}/decision", json={"decision": "approve", "review_seconds": 18}, headers=h)
    r2 = c.post(f"/api/v1/approvals/{mid}/decision", json={"decision": "approve", "review_seconds": 18}, headers=h)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM approvals WHERE reviewer_id = :u"), {"u": demo.operator_id}
            ).scalar()
            == 3
        )  # one row per sequence message, not doubled
    r3 = c.post(f"/api/v1/approvals/{mid}/decision", json={"decision": "reject", "reason": "changed mind"}, headers=h)
    assert r3.status_code == 422  # same key, different request
    r4 = c.post(
        f"/api/v1/approvals/{mid}/decision",
        json={"decision": "reject", "reason": "again"},
        headers={"Idempotency-Key": "k-2"},
    )
    assert r4.status_code == 409  # already approved


def test_reject_without_reason_and_masked_edit_are_refused(world) -> None:  # type: ignore[no-untyped-def]
    c, _ = world
    item = c.get("/api/v1/approvals").json()["items"][1]
    r = c.post(
        f"/api/v1/approvals/{item['message_id']}/decision",
        json={"decision": "reject"},
        headers={"Idempotency-Key": "r-1"},
    )
    assert r.status_code == 409 and "reason" in r.json()["detail"]
    masked_body = item["sequence"][0]["body"]
    r = c.post(
        f"/api/v1/approvals/{item['message_id']}/decision",
        json={"decision": "edit", "edits": [{"message_id": item["message_id"], "body": masked_body}]},
        headers={"Idempotency-Key": "e-1"},
    )
    assert r.status_code == 409 and "masked" in r.json()["detail"]


def test_tenant_users_cannot_use_the_approval_queue(world, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    c, demo = world
    make_tenant_user(clean_db, settings, cipher, demo.tenant_id, email="admin@northwind.example")
    t = TestClient(c.app)
    r = t.post("/api/v1/auth/login", json={"email": "admin@northwind.example", "password": "tenant-password-123"})
    assert r.status_code == 200
    t.headers["x-csrf-token"] = t.cookies["pos_csrf"]
    assert t.get("/api/v1/approvals").status_code == 403
    assert t.get("/api/v1/dry-run").status_code == 403
    assert t.post("/api/v1/send/run", json={"tenant_id": str(demo.tenant_id)}).status_code == 403
    assert t.get("/api/v1/pipeline/summary").status_code == 200  # tenant can see its own pipeline counts


def test_summary_send_and_logs(world) -> None:  # type: ignore[no-untyped-def]
    c, demo = world
    s = c.get("/api/v1/pipeline/summary").json()
    assert s["pending_approval"] == 5 and s["real_sends"] == 0 and s["llm_calls"] == {"simulated": 23}
    assert s["by_state"]["alternative_channel"] == 2 and s["open_alternative_tasks"] == 2
    for i in c.get("/api/v1/approvals").json()["items"]:
        c.post(
            f"/api/v1/approvals/{i['message_id']}/decision",
            json={"decision": "approve"},
            headers={"Idempotency-Key": f"a-{i['message_id']}"},
        )
    sent = c.post("/api/v1/send/run", json={"tenant_id": str(demo.tenant_id)}).json()
    # sent immediately if inside UK business hours, otherwise deferred to the next window — never real
    assert set(sent["results"]) <= {"dry_run_sent", "deferred"} and sum(sent["results"].values()) == 5
    assert sent["modes"] in ([], ["dry_run"])
    log = c.get("/api/v1/compliance/log").json()
    assert any(r["outcome"] == "alternative_channel" and "CONSENT_REQUIRED" in r["reason_codes"] for r in log)
    assert all("@" not in (r["prospect"] or "") for r in log)
    for row in c.get("/api/v1/dry-run").json():
        assert "***" in row["recipient"]
