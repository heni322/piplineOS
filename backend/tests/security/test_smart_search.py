"""Smart company search: the AI writes the Places searches from the client's targeting and ranks the companies the
usual checks confirmed. It never confirms a company itself, it plans even before go-live (so the operator sees the
searches) without reaching Google, and a client with no targeting cannot run one. In-process transports only; the AI
is the deterministic simulator."""

from __future__ import annotations

import uuid

import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.paid import searches
from app.domain.tenants.config import load_config, save_config
from tests.security.test_company_searches import REGISTER, _row, finder
from tests.security.test_golive_prep import _login
from tests.security.test_places import gated

pytestmark = pytest.mark.db


def _queue_smart(engine, tenant_id: uuid.UUID) -> uuid.UUID:  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator", tenant_id=tenant_id), engine) as conn:
        op = conn.execute(text("SELECT id FROM users ORDER BY role = 'operator' DESC LIMIT 1")).scalar()
        return searches.request(conn, tenant_id=tenant_id, operator_id=op, query="", country="GB", pages=1, smart=True)


def _keywords(engine, tenant_id: uuid.UUID) -> list[str]:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        return load_config(conn, tenant_id)[1].icp.industry_keywords


def test_smart_search_plans_from_targeting_searches_each_and_ranks(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []
    places = gated(clean_db, settings, cipher, "live", seen)
    tenant = places.gate.tenant_id
    sid = _queue_smart(clean_db, tenant)
    s = settings.model_copy(update={"smart_search_max_queries": 3})
    assert searches.run(clean_db, s, cipher, sid, tenant, places=places, finder=finder(), registry=REGISTER) == "done"
    row = _row(clean_db, sid)
    report = row["report"]
    assert row["smart"] is True and report["smart"] is True and row["query"] == searches.SMART_LABEL
    # the AI's searches come from the client's own targeting, one billed request per search
    assert len(report["queries"]) == 3 and len(seen) == 3
    assert all(any(k.lower() in q.lower() for k in _keywords(clean_db, tenant)) for q in report["queries"])
    # the same confirmed company found by several searches is listed once, with its fit score and reason
    (found,) = report["found"]
    assert found["company_number"] == "01234567" and isinstance(found["fit_score"], int) and found["fit_reason"]
    assert report["ranking"] == "done"


def test_before_go_live_the_plan_is_shown_but_google_is_not_called(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []
    places = gated(clean_db, settings, cipher, "dry_run", seen)
    tenant = places.gate.tenant_id
    sid = _queue_smart(clean_db, tenant)
    assert searches.run(clean_db, settings, cipher, sid, tenant, places=places, registry=REGISTER) == "dry_run"
    row = _row(clean_db, sid)
    assert seen == [] and row["called_google"] is False
    assert row["report"]["queries"] and row["report"]["found"] == []


def test_a_client_without_targeting_cannot_run_a_smart_search(clean_db, settings, cipher, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    places = gated(clean_db, settings, cipher, "live", [])
    tenant = places.gate.tenant_id
    sid = _queue_smart(clean_db, tenant)
    monkeypatch.setattr(searches, "_config", lambda engine, tenant_id: None)
    assert searches.run(clean_db, settings, cipher, sid, tenant, places=places, registry=REGISTER) == "failed"
    assert "targeting" in (_row(clean_db, sid)["error"] or "")


def test_smart_search_needs_the_ai_key_when_gemini_is_chosen(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    places = gated(clean_db, settings, cipher, "live", [])
    tenant = places.gate.tenant_id
    sid = _queue_smart(clean_db, tenant)
    s = settings.model_copy(update={"llm_adapter_mode": "sandbox", "llm_sandbox_provider": "gemini"})
    assert searches.run(clean_db, s, cipher, sid, tenant, places=places, registry=REGISTER) == "failed"
    assert "GEMINI_API_KEY" in (_row(clean_db, sid)["error"] or "")


def test_api_smart_search_needs_no_query_and_reports_the_ai(clean_db, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    op = _login(
        TestClient(create_app()),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )
    url = f"/api/v1/clients/{demo.tenant_id}/company-searches"
    monkeypatch.setattr(searches, "dispatch", lambda sid, tid: None)
    r = op.post(url, json={"smart": True, "country": "GB"})
    assert r.status_code == 202 and r.json()["smart"] is True and r.json()["query"] == searches.SMART_LABEL
    assert op.post(url, json={"country": "GB"}).status_code == 422  # a manual search still needs a query
    setup = op.get(url).json()["setup"]
    assert setup["ai"] == "simulated" and setup["ai_key_set"] is False


def test_a_smart_search_uses_the_clients_own_country(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    """A French client asking from a form left on GB still searches France (no Companies House key needed)."""
    demo = seed_demo(clean_db, settings)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        cfg = load_config(conn, demo.tenant_id)[1]
        save_config(
            conn, demo.tenant_id, cfg.model_copy(update={"icp": cfg.icp.model_copy(update={"countries": ["FR"]})})
        )
    with transaction(DbContext(role="operator", tenant_id=demo.tenant_id), clean_db) as conn:
        op = conn.execute(text("SELECT id FROM users ORDER BY role = 'operator' DESC LIMIT 1")).scalar()
        smart = searches.request(
            conn, tenant_id=demo.tenant_id, operator_id=op, query="", country="GB", pages=1, smart=True
        )
        manual = searches.request(
            conn, tenant_id=demo.tenant_id, operator_id=op, query="IT Leeds", country="GB", pages=1
        )
    assert _row(clean_db, smart)["country"] == "FR"  # the client's country
    assert _row(clean_db, manual)["country"] == "GB"  # a manual search keeps the country chosen


def test_a_uk_search_without_the_key_says_what_to_do(settings) -> None:  # type: ignore[no-untyped-def]
    from app.adapters.base import AdapterNotConfigured

    with pytest.raises(AdapterNotConfigured, match="choose France"):
        searches.registry_for(settings.model_copy(update={"companies_house_api_key": None}), "GB")
    assert searches.registry_for(settings.model_copy(update={"companies_house_api_key": None}), "FR") is not None
