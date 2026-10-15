"""Phase 5 API: invoices (drafts operator-only), MFA for issue/void/accountant sign-off, client disputes and terms,
onboarding endpoints, reports scoped to the caller's tenant."""

from __future__ import annotations

from datetime import date

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.conversations import run_conversations
from app.demo.dataset import DEMO_CONFIG
from app.demo.seed import providers, seed_demo
from app.workflows.pipeline import agent_context, run_campaign

pytestmark = pytest.mark.db


def _login(c: TestClient, email: str, password: str, totp: str | None = None) -> TestClient:
    body = {"email": email, "password": password} | ({"totp_code": totp} if totp else {})
    r = c.post("/api/v1/auth/login", json=body)
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = c.cookies["pos_csrf"]
    return c


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    run_conversations(ctx, providers(), demo.operator_id)
    app = create_app()
    op = _login(
        TestClient(app),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )
    client = _login(TestClient(app), demo.client_email, demo.client_password or "")
    return op, client, demo


def _draft(op: TestClient, demo) -> dict:  # type: ignore[no-untyped-def, type-arg]
    r = op.post("/api/v1/invoices/draft", json={"tenant_id": str(demo.tenant_id), "period_start": "2026-10-01"})
    assert r.status_code == 200, r.text
    return r.json()


def test_operator_drafts_and_client_only_sees_issued_invoices(world) -> None:  # type: ignore[no-untyped-def]
    op, client, demo = world
    d = _draft(op, demo)
    assert d["total_minor"] == 105_000 and "TAX_RULE_NOT_VERIFIED_BY_ACCOUNTANT" in d["blocked_reasons"]
    assert (
        client.post(
            "/api/v1/invoices/draft", json={"tenant_id": str(demo.tenant_id), "period_start": "2026-10-01"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/invoices").json() == []  # drafts are the operator's working copy
    assert client.get(f"/api/v1/invoices/{d['invoice_id']}").status_code == 404
    inv = op.get(f"/api/v1/invoices/{d['invoice_id']}").json()
    assert [line["kind"] for line in inv["lines"]] == ["setup", "retainer", "meeting"]
    assert inv["lines"][2]["evidence"]["attendance_event_id"]


def test_issuing_requires_mfa_and_reports_what_is_missing(world) -> None:  # type: ignore[no-untyped-def]
    op, _, demo = world
    d = _draft(op, demo)
    url = f"/api/v1/invoices/{d['invoice_id']}/issue"
    assert op.post(url, json={"totp_code": "000000"}).status_code == 403
    r = op.post(url, json={"totp_code": pyotp.TOTP(demo.operator_totp_secret).now()})
    assert r.status_code == 409 and "GO_LIVE_NOT_SIGNED_OFF" in r.json()["detail"]


def test_accountant_signoff_needs_mfa_and_is_operator_only(world) -> None:  # type: ignore[no-untyped-def]
    op, client, demo = world
    body = {"accountant": "A. Accountant, Example LLP", "reviewed_on": "2026-10-30", "totp_code": "000000"}
    url = "/api/v1/tax-rules/uk_not_vat_registered/accountant-verification"
    assert client.post(url, json=body).status_code == 403
    assert op.post(url, json=body).status_code == 403
    r = op.post(url, json=body | {"totp_code": pyotp.TOTP(demo.operator_totp_secret).now()})
    assert r.status_code == 201
    rules = {x["code"]: x for x in op.get("/api/v1/tax-rules").json()}
    assert rules["uk_not_vat_registered"]["verified_by_accountant"] is True
    assert rules["uk_vat_standard_b2b_uk"]["verified_by_accountant"] is False


def test_client_disputes_and_operator_resolves(world) -> None:  # type: ignore[no-untyped-def]
    op, client, _ = world
    meetings = client.get("/api/v1/billing/meetings").json()
    contoso = next(m for m in meetings if m["company"].startswith("Contoso"))
    assert contoso["billable"] is True
    short = client.post("/api/v1/disputes", json={"meeting_id": contoso["meeting_id"], "kind": "other", "reason": "x"})
    assert short.status_code == 422
    body = {
        "meeting_id": contoso["meeting_id"],
        "kind": "qualification",
        "reason": "They said budget was not approved yet.",
    }
    assert op.post("/api/v1/disputes", json=body).status_code == 403  # only the client raises disputes
    r = client.post("/api/v1/disputes", json=body)
    assert r.status_code == 200
    assert client.post("/api/v1/disputes", json=body).status_code == 409  # one open dispute per meeting
    did = r.json()["id"]
    assert (
        client.post(f"/api/v1/disputes/{did}/resolve", json={"outcome": "upheld", "note": "self-approval"}).status_code
        == 403
    )
    res = op.post(f"/api/v1/disputes/{did}/resolve", json={"outcome": "upheld", "note": "Budget not confirmed."})
    assert res.status_code == 200 and res.json()["status"] == "upheld"
    after = next(m for m in client.get("/api/v1/billing/meetings").json() if m["meeting_id"] == contoso["meeting_id"])
    assert after["billable"] is False and after["reasons"] == ["DISPUTE_UPHELD"]
    assert client.get("/api/v1/disputes").json()[0]["status"] == "upheld"


def test_client_accepts_terms_once_and_cannot_reach_operator_endpoints(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    op, client, demo = world
    terms = {t["kind"]: t for t in client.get("/api/v1/terms").json()}
    assert terms["pricing"]["signed"] and terms["pricing"]["content"]["per_meeting_minor"] == 30_000
    assert client.post("/api/v1/terms/pricing/sign").status_code == 409  # already accepted
    cfg = DEMO_CONFIG.model_dump(mode="json")
    cfg["pricing"]["per_meeting_minor"] = 35_000
    assert op.put(f"/api/v1/clients/{demo.tenant_id}/config", json=cfg).status_code == 200
    assert {t["kind"]: t for t in client.get("/api/v1/terms").json()}["pricing"]["signed"] is False
    assert client.post("/api/v1/terms/pricing/sign").status_code == 201
    assert op.post("/api/v1/terms/pricing/sign").status_code == 403  # only the client accepts in the portal
    for method, path in (
        ("get", "/api/v1/clients"),
        ("post", f"/api/v1/clients/{demo.tenant_id}/activate"),
        ("get", "/api/v1/billing/vat-threshold"),
        ("get", "/api/v1/tax-rules"),
    ):
        assert getattr(client, method)(path).status_code == 403, path
    bad = op.put(f"/api/v1/clients/{demo.tenant_id}/config", json={"niche_slug": "x"})
    assert bad.status_code == 422


def test_onboarding_endpoints(world) -> None:  # type: ignore[no-untyped-def]
    op, _, _ = world
    r = op.post(
        "/api/v1/clients",
        json={
            "slug": "tailspin-msp",
            "legal_name": "Tailspin MSP Ltd",
            "company_number": "22222222",
            "registered_address": "3 Example Way, Hull",
        },
    )
    assert r.status_code == 201
    c = r.json()
    assert c["status"] == "onboarding" and not next(i for i in c["checklist"] if i["key"] == "lia")["done"]
    assert op.post("/api/v1/clients", json={"slug": "tailspin-msp", "legal_name": "Dup Ltd"}).status_code == 409
    act = op.post(f"/api/v1/clients/{c['id']}/activate")
    assert act.status_code == 409 and "criteria_signed" in act.json()["detail"]
    assert (
        op.post(
            f"/api/v1/clients/{c['id']}/lia", json={"reference": "LIA-T-1", "approved_on": "2026-10-01"}
        ).status_code
        == 204
    )
    u = op.post(f"/api/v1/clients/{c['id']}/users", json={"email": "admin@tailspin.example"})
    assert u.status_code == 201 and u.json()["status"] == "invited" and "one_time_password" not in u.json()
    assert u.json()["dev_invite_link"].startswith("http://localhost:3000/set-password?token=")
    clients = {x["slug"]: x for x in op.get("/api/v1/clients").json()}
    items = {i["key"]: i["done"] for i in clients["tailspin-msp"]["checklist"]}
    assert items["lia"] and items["client_user"] and not items["dpa"]


def test_reports_are_scoped_to_the_callers_tenant(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    op, client, demo = world
    r = client.get("/api/v1/reports/metrics", params={"start": "2026-01-01", "end": "2026-12-31"})
    assert r.status_code == 200 and r.json()["meetings_attended"] == 1
    with transaction(DbContext(role="operator"), clean_db) as conn:
        other = conn.execute(
            text("INSERT INTO tenants (slug, legal_name) VALUES ('x-co', 'X Ltd') RETURNING id")
        ).scalar()
    # a client can't ask for another tenant's numbers: the parameter is ignored for tenant users
    r2 = client.get(
        "/api/v1/reports/metrics", params={"tenant_id": str(other), "start": "2026-01-01", "end": "2026-12-31"}
    )
    assert r2.json()["meetings_attended"] == 1
    assert op.get("/api/v1/reports/metrics").status_code == 422  # operators must pick a client
    assert (
        op.get(
            "/api/v1/reports/metrics",
            params={"tenant_id": str(demo.tenant_id), "start": "2026-12-01", "end": "2026-01-01"},
        ).status_code
        == 422
    )
    assert date.fromisoformat(r.json()["period"]["start"]) == date(2026, 1, 1)


def test_anonymous_requests_are_refused(world) -> None:  # type: ignore[no-untyped-def]
    op, _, _ = world
    anon = TestClient(op.app)
    for path in ("/api/v1/invoices", "/api/v1/disputes", "/api/v1/terms", "/api/v1/reports/metrics", "/api/v1/clients"):
        assert anon.get(path).status_code == 401, path


def test_client_sets_their_own_roi_figures(world) -> None:  # type: ignore[no-untyped-def]
    op, client, _demo = world
    params = {"start": "2026-01-01", "end": "2026-12-31"}
    assert client.get("/api/v1/reports/metrics", params=params).json()["roi"] == {"status": "not_configured"}
    assert (
        client.put("/api/v1/account/roi", json={"average_deal_value_minor": 1, "close_rate_pct": 101}).status_code
        == 422
    )
    r = client.put("/api/v1/account/roi", json={"average_deal_value_minor": 2_000_000, "close_rate_pct": 25})
    assert r.status_code == 204
    m = client.get("/api/v1/reports/metrics", params=params).json()
    roi = m["roi"]
    assert roi["status"] == "estimate" and roi["close_rate_pct"] == 25
    assert roi["pipeline_value_minor"] == m["qualified_meetings"] * 2_000_000
    assert roi["expected_revenue_minor"] == roi["pipeline_value_minor"] // 4
    # Only the client's admin sets it; the operator uses the configuration instead.
    assert op.put("/api/v1/account/roi", json={"average_deal_value_minor": 1, "close_rate_pct": 1}).status_code == 403
    # Signed terms are untouched by the new configuration version.
    assert all(t["signed"] for t in client.get("/api/v1/terms").json())
