"""Find companies (console): an operator starts a search, a worker runs it once, and the result says plainly whether
Google was called. Before go-live nothing reaches Google; a missing key or FREE_ONLY ends the search with the reason;
only confirmed companies are kept (nothing Google returned); client accounts can neither start nor see searches;
only the worker can write a result. In-process transports only."""

from __future__ import annotations

import json
import uuid

import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.companies_house.base import RegistryCompany
from app.adapters.website_contacts import WebsiteFinder
from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.paid import searches
from app.domain.paid.discovery import discover_companies
from tests.security.test_golive_prep import _login
from tests.security.test_places import CONTACT, SITE, Register, gated

pytestmark = pytest.mark.db

PAGES = {"lakesidenetworks.co.uk/": SITE, "lakesidenetworks.co.uk/contact": CONTACT}
REGISTER = Register({"01234567": RegistryCompany("01234567", "LAKESIDE NETWORKS LIMITED", "ltd", "active", "GB")})


def finder() -> WebsiteFinder:
    def handler(req: httpx.Request) -> httpx.Response:
        body = PAGES.get(f"{req.url.host}{req.url.path}")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"}) if body else httpx.Response(404)

    return WebsiteFinder(transport=httpx.MockTransport(handler), public_host=lambda h: True, mx=lambda d: True)


def _queue(engine, tenant_id: uuid.UUID, query: str = "IT support Leeds") -> uuid.UUID:  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator", tenant_id=tenant_id), engine) as conn:
        op = conn.execute(text("SELECT id FROM users ORDER BY role = 'operator' DESC LIMIT 1")).scalar()
        return searches.request(conn, tenant_id=tenant_id, operator_id=op, query=query, country="GB", pages=1)


def _row(engine, search_id: uuid.UUID) -> dict:  # type: ignore[no-untyped-def, type-arg]
    with transaction(DbContext(role="operator"), engine) as conn:
        return dict(
            conn.execute(text("SELECT * FROM company_searches WHERE id = :i"), {"i": search_id}).mappings().one()
        )


def test_live_search_keeps_confirmed_companies_only_and_runs_once(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []
    places = gated(clean_db, settings, cipher, "live", seen)
    tenant = places.gate.tenant_id
    sid = _queue(clean_db, tenant)
    status = searches.run(clean_db, settings, cipher, sid, tenant, places=places, finder=finder(), registry=REGISTER)
    assert status == "done" and len(seen) == 1
    row = _row(clean_db, sid)
    assert row["status"] == "done" and row["called_google"] is True and row["finished_at"] is not None
    (found,) = row["report"]["found"]
    assert found == {
        "name": "LAKESIDE NETWORKS LIMITED",
        "company_number": "01234567",
        "domain": "lakesidenetworks.co.uk",
        "address": "sales@lakesidenetworks.co.uk",
        "country": "GB",
    }
    stored = json.dumps(row["report"])
    assert "1 High St" not in stored and "p1" not in stored  # nothing Google returned (address, place ID) is kept
    # a redelivered task finds the search no longer queued: no second billed request
    assert searches.run(clean_db, settings, cipher, sid, tenant, places=places, finder=finder(), registry=REGISTER) == (
        "skipped"
    )
    assert len(seen) == 1


def test_before_go_live_nothing_reaches_google(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []
    places = gated(clean_db, settings, cipher, "dry_run", seen)
    sid = _queue(clean_db, places.gate.tenant_id)
    assert searches.run(clean_db, settings, cipher, sid, places.gate.tenant_id, places=places, registry=REGISTER) == (
        "dry_run"
    )
    row = _row(clean_db, sid)
    assert seen == [] and row["called_google"] is False and row["report"]["found"] == []


def test_free_only_or_no_key_fails_with_the_reason(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    sid = _queue(clean_db, demo.tenant_id)
    s = settings.model_copy(update={"free_only": True})
    assert searches.run(clean_db, s, cipher, sid, demo.tenant_id, registry=REGISTER) == "failed"
    assert "FREE_ONLY" in (_row(clean_db, sid)["error"] or "")


def test_time_limit_keeps_what_was_confirmed(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    places = gated(clean_db, settings, cipher, "live", [])
    ticks = iter([0.0, 0.0, 5.0, 100.0, 100.0, 100.0])
    report = discover_companies(
        places, finder(), REGISTER, "IT Leeds", country="GB", time_limit=10, clock=lambda: next(ticks)
    )
    assert report.stopped_early and report.searched == 2  # the third business was not checked


def test_api_operator_only_and_queue_failure_is_reported(clean_db, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    app = create_app()
    op = _login(
        TestClient(app),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )
    url = f"/api/v1/clients/{demo.tenant_id}/company-searches"
    dispatched: list[uuid.UUID] = []
    monkeypatch.setattr(searches, "dispatch", lambda sid, tid: dispatched.append(sid))
    r = op.post(url, json={"query": "agence web Lyon", "country": "FR"})
    assert r.status_code == 202 and r.json()["status"] == "queued" and len(dispatched) == 1
    listing = op.get(url).json()
    assert listing["setup"]["monthly_cap"] == settings.places_monthly_cap and listing["searches"][0]["query"] == (
        "agence web Lyon"
    )
    assert op.post(url, json={"query": "x", "country": "GB"}).status_code == 422
    assert op.post(url, json={"query": "IT Leeds", "country": "DE"}).status_code == 422
    assert op.post(url.replace(str(demo.tenant_id), str(uuid.uuid4())), json={"query": "IT Leeds"}).status_code == 404

    def down(sid, tid):  # type: ignore[no-untyped-def]
        raise ConnectionError("broker down")

    monkeypatch.setattr(searches, "dispatch", down)
    r = op.post(url, json={"query": "IT support Leeds"})
    assert r.json()["status"] == "failed" and "job queue" in r.json()["error"]

    client = _login(TestClient(app), demo.client_email, demo.client_password or "")
    assert client.get(url).status_code == 403
    assert client.post(url, json={"query": "IT support Leeds"}).status_code == 403


def test_only_the_worker_writes_results(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    sid = _queue(clean_db, demo.tenant_id)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        hit = conn.execute(text("UPDATE company_searches SET status = 'done' WHERE id = :i"), {"i": sid}).rowcount
    assert hit == 0 and _row(clean_db, sid)["status"] == "queued"
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'places.search_requested'")).scalar() == 1
        )
