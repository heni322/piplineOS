"""Contact-tasks API: the operator's queue of leads to reach by hand (free-only register contacts with no email,
and alternative-channel leads). Names masked, operator-only, closed once with a note kept off the audit log."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.companies_house.base import RegistryCompany
from app.adapters.companies_house.mock import MockCompaniesHouse
from app.adapters.companies_house.officers import CompaniesHouseOfficers
from app.adapters.registry import providers_for, signal_query
from app.adapters.signals.ocds import contracts_finder
from app.agents.trigger_hunter.agent import SourceBinding
from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.workflows.pipeline import agent_context, run_campaign
from tests.factories import make_tenant_user
from tests.security.test_free_only import OFFICERS, free
from tests.security.test_paid_gate import COMPANY, NOTICES, NUMBER

pytestmark = pytest.mark.db


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    s = free(settings).model_copy(update={"website_contacts": False})
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    p = providers_for(s, ctx)
    p.sources = [
        SourceBinding(
            contracts_finder(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=NOTICES))),
            signal_query(ctx.config),
        )
    ]
    p.enrichment = CompaniesHouseOfficers(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=OFFICERS))
    )
    p.registry = MockCompaniesHouse({COMPANY: RegistryCompany(NUMBER, COMPANY, "ltd", "active", "GB")})
    p.insolvency = None
    run_campaign(ctx, p, since=datetime(2026, 10, 1, tzinfo=UTC))
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


def test_operator_sees_the_published_contact_task_masked(world) -> None:  # type: ignore[no-untyped-def]
    c, _ = world
    r = c.get("/api/v1/tasks")
    assert r.status_code == 200
    [task] = r.json()
    assert task["channel"] == "published_contact" and task["status"] == "open"
    assert task["company"] == COMPANY and task["company_number"] == NUMBER
    assert task["prospect_title"] == "Operations Director"
    assert task["prospect"] and "Reyes" not in r.text and "Dana" not in r.text  # masked
    assert task["signal_url"] and "No automated messaging" in task["instructions"]


def test_a_task_closes_once_and_the_note_stays_off_the_audit_log(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    c, _ = world
    [task] = c.get("/api/v1/tasks").json()
    url = f"/api/v1/tasks/{task['id']}/complete"
    assert c.post(url, json={"outcome": "done", "note": "x"}).status_code == 422
    assert c.post(url, json={"outcome": "maybe", "note": "posted a letter"}).status_code == 422
    assert (
        c.post(url, json={"outcome": "done", "note": "Letter to Dana Reyes at the registered office"}).status_code
        == 200
    )
    assert c.post(url, json={"outcome": "skipped", "note": "again please"}).status_code == 409
    assert c.get("/api/v1/tasks").json() == []
    [closed] = c.get("/api/v1/tasks?status=done").json()
    assert closed["outcome_note"].startswith("Letter to")
    with transaction(DbContext(role="operator"), clean_db) as conn:
        audit = conn.execute(text("SELECT after FROM audit_log WHERE action = 'alternative_task.done'")).scalar_one()
    assert audit == {"outcome": "done"} and "Reyes" not in str(audit)


def test_client_users_cannot_see_or_close_tasks(world, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    c, demo = world
    [task] = c.get("/api/v1/tasks").json()
    make_tenant_user(clean_db, settings, cipher, demo.tenant_id, email="admin@northwind.example")
    t = TestClient(c.app)
    r = t.post("/api/v1/auth/login", json={"email": "admin@northwind.example", "password": "tenant-password-123"})
    assert r.status_code == 200
    t.headers["x-csrf-token"] = t.cookies["pos_csrf"]
    assert t.get("/api/v1/tasks").status_code == 403
    assert t.post(f"/api/v1/tasks/{task['id']}/complete", json={"outcome": "done", "note": "nope"}).status_code == 403
    assert TestClient(c.app).get("/api/v1/tasks").status_code == 401
