"""Paid data lookups (Hunter) follow the brief's rule for paid API calls: nothing reaches the provider unless the
adapter mode is live, the client is live AND global go-live is on with the checklist signed. Until then the would-be
call is written (encrypted) to the dry-run log and the lead waits: it is never disqualified for want of a lookup.
Also: real trigger sources (public award notices) feed the pipeline end to end, a person who objected at the data
source is suppressed, and a company resolved to a domain the client already has is not worked twice.

Every HTTP request goes to an in-process transport; nothing touches the network."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.base import AdapterNotConfigured
from app.adapters.companies_house.base import RegistryCompany
from app.adapters.companies_house.mock import MockCompaniesHouse
from app.adapters.registry import providers_for, signal_query
from app.adapters.signals.ocds import contracts_finder
from app.agents.trigger_hunter.agent import SourceBinding
from app.core.crypto import email_domain
from app.db.session import DbContext, transaction
from app.demo.seed import providers, seed_demo
from app.domain.paid.providers import enrichment_for, verifier_for
from app.domain.suppression.service import RecipientHashes
from app.workflows.pipeline import agent_context, hunt_active_campaigns, run_campaign

pytestmark = pytest.mark.db
SINCE = datetime(2026, 10, 1, tzinfo=UTC)
COMPANY = "Lakeside Networks Ltd"
NUMBER = "01234567"
CONTACT = "dana.reyes@lakeside-net.example"


def notice(ocid: str, title: str) -> dict[str, Any]:
    supplier = {
        "id": f"GB-COH-{NUMBER}",
        "name": COMPANY,
        "identifier": {"legalName": COMPANY, "scheme": "GB-COH", "id": NUMBER},
        "address": {"countryName": "England"},
        "roles": ["supplier"],
    }
    return {
        "ocid": ocid,
        "id": ocid,
        "date": "2026-10-12T14:00:00+01:00",
        "tag": ["award"],
        "tender": {"title": title, "description": "Managed IT and service desk support for council offices."},
        "buyer": {"id": "B1", "name": "Northshire County Council"},
        "parties": [supplier],
        "awards": [{"id": "A1", "suppliers": [{"id": supplier["id"], "name": COMPANY}]}],
    }


NOTICES = {
    "releases": [
        notice("ocds-b5fd17-0001", "IT support and service desk"),
        notice("ocds-b5fd17-0002", "Managed service: network refresh"),  # same supplier: one company, one lead
    ],
    "links": {},
}


class Hunter:
    """Fake Hunter: records every request; the verifier answer is configurable."""

    def __init__(self, verify_status: int = 200) -> None:
        self.calls: list[httpx.Request] = []
        self.verify_status = verify_status

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.url.path == "/v2/domain-search":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "domain": "lakeside-net.example",
                        "emails": [
                            {
                                "value": CONTACT,
                                "confidence": 93,
                                "first_name": "Dana",
                                "last_name": "Reyes",
                                "position": "Head of Service Delivery",
                                "seniority": "senior",
                                "sources": [{"uri": "https://lakeside-net.example/team"}],
                            }
                        ],
                    }
                },
            )
        if req.url.path == "/v2/people/find":
            return httpx.Response(200, json={"data": {"location": "Leeds, England, United Kingdom"}})
        if req.url.path == "/v2/email-verifier":
            return httpx.Response(self.verify_status, json={"data": {"status": "valid"}})
        return httpx.Response(404)


def live_providers(settings, ctx, hunter: Hunter):  # type: ignore[no-untyped-def]
    p = providers()
    p.sources = [
        SourceBinding(
            contracts_finder(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=NOTICES))),
            signal_query(ctx.config),
        )
    ]
    t = httpx.MockTransport(hunter)
    p.enrichment = enrichment_for(settings, ctx.engine, ctx.cipher, ctx.tenant_id, transport=t)
    p.verifier = verifier_for(settings, ctx.engine, ctx.cipher, ctx.tenant_id, transport=t)
    p.registry = MockCompaniesHouse({COMPANY: RegistryCompany(NUMBER, COMPANY, "ltd", "active", "GB")})
    return p


def modes(settings, mode: str, **extra: Any):  # type: ignore[no-untyped-def]
    return settings.model_copy(
        update={
            "enrichment_adapter_mode": mode,
            "verifier_adapter_mode": mode,
            "hunter_api_key": SecretStr("real-key"),
            **extra,
        }
    )


def lead_rows(engine, tenant_id):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        return conn.execute(
            text(
                "SELECT l.state, c.domain_normalised, c.company_number, l.prospect_id FROM leads l "
                "JOIN companies c ON c.id = l.company_id WHERE c.company_number = :n"
            ),
            {"n": NUMBER},
        ).all()


def test_paid_lookups_wait_in_the_dry_run_log_until_all_three_keys_are_on(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    s = modes(settings, "live")  # key 1 only: the client is not live, go-live not signed
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    hunter = Hunter()
    run = run_campaign(ctx, live_providers(s, ctx, hunter), since=SINCE)
    run_campaign(ctx, live_providers(s, ctx, hunter), since=SINCE)  # a later scheduled run retries: still waits

    assert hunter.calls == []  # not one paid request
    assert run.hunt.fetched == 2 and run.hunt.discovered == 1  # two notices, one company
    rows = lead_rows(clean_db, demo.tenant_id)
    assert [(r.state, r.domain_normalised, r.company_number) for r in rows] == [("discovered", None, NUMBER)]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        paid = conn.execute(
            text("SELECT adapter, entity_id, payload_enc FROM dry_run_log WHERE effect_type = 'paid_api_call'")
        ).all()
        payload = cipher.decrypt(conn, demo.tenant_id, "dry_run_log.payload", paid[0].payload_enc)
    assert [p.adapter for p in paid] == ["hunter.enrichment"]  # recorded once, even after the second run
    assert COMPANY in payload and COMPANY.encode() not in bytes(paid[0].payload_enc)  # encrypted at rest


def test_sandbox_uses_the_free_test_key_and_resolves_the_domain(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    s = modes(settings, "sandbox")
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    hunter = Hunter()
    run_campaign(ctx, live_providers(s, ctx, hunter), since=SINCE)

    assert hunter.calls and {c.headers["X-API-KEY"] for c in hunter.calls} == {"test-api-key"}
    (row,) = lead_rows(clean_db, demo.tenant_id)
    assert row.domain_normalised == "lakeside-net.example" and row.prospect_id is not None
    assert row.state not in ("discovered", "disqualified")
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        p = conn.execute(
            text("SELECT country_code, subdivision_code, email_verification FROM prospects WHERE id = :p"),
            {"p": row.prospect_id},
        ).one()
        sources = set(
            conn.execute(
                text("SELECT data_source FROM prospect_field_sources WHERE prospect_id = :p"), {"p": row.prospect_id}
            ).scalars()
        )
    assert (p.country_code, p.subdivision_code, p.email_verification) == ("GB", "GB-ENG", "valid")
    assert any(x.startswith("hunter.io domain-search lakeside-net.example") for x in sources)
    assert "hunter.verifier" in sources


def test_real_lookups_happen_only_with_all_three_keys(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    with transaction(DbContext(role="operator", user_id=demo.operator_id), clean_db) as conn:
        conn.execute(text("UPDATE tenants SET live_mode = true WHERE id = :t"), {"t": demo.tenant_id})  # key 2
    s = modes(settings, "live")
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    hunter = Hunter()
    run_campaign(ctx, live_providers(s, ctx, hunter), since=SINCE)
    assert hunter.calls == []  # global go-live still off

    with transaction(DbContext(role="operator", user_id=demo.operator_id), clean_db) as conn:  # key 3 (test DB only)
        for item in conn.execute(text("SELECT id FROM golive_checklist_items")).scalars():
            conn.execute(text("SELECT golive_sign_off(:i, 'https://evidence.example')"), {"i": item})
    all_on = modes(settings, "live", global_go_live=True)
    ctx = agent_context(clean_db, all_on, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, live_providers(all_on, ctx, hunter), since=SINCE)
    assert hunter.calls and {c.headers["X-API-KEY"] for c in hunter.calls} == {"real-key"}
    assert all("real-key" not in str(c.url) for c in hunter.calls)
    (row,) = lead_rows(clean_db, demo.tenant_id)
    assert row.prospect_id is not None and row.domain_normalised == "lakeside-net.example"


def test_a_person_who_objected_at_the_source_is_suppressed_and_never_contacted(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    s = modes(settings, "sandbox")
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, live_providers(s, ctx, Hunter(verify_status=451)), since=SINCE)
    (row,) = lead_rows(clean_db, demo.tenant_id)
    assert row.state == "disqualified" and row.prospect_id is None  # nothing about the person is stored
    h = RecipientHashes.for_email(settings.key("suppression_hash_key"), CONTACT)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        assert (
            conn.execute(
                text("SELECT reason FROM suppression_list WHERE identifier_hash = :h AND scope = 'global'"),
                {"h": h.email},
            ).scalar()
            == "objection"
        )
    assert email_domain(CONTACT) == "lakeside-net.example"


def test_a_company_resolving_to_a_known_domain_is_not_worked_twice(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        conn.execute(
            text("""INSERT INTO companies (tenant_id, name, domain, domain_normalised, country_code)
                    VALUES (:t, 'Lakeside Networks', 'lakeside-net.example', 'lakeside-net.example', 'GB')"""),
            {"t": demo.tenant_id},
        )
    s = modes(settings, "sandbox")
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, live_providers(s, ctx, Hunter()), since=SINCE)
    (row,) = lead_rows(clean_db, demo.tenant_id)
    assert row.state == "disqualified" and row.domain_normalised is None


def test_real_providers_need_a_client_context_and_a_key(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AdapterNotConfigured):
        providers_for(settings.model_copy(update={"signals_adapter_mode": "live"}))
    demo = seed_demo(clean_db, settings)
    no_key = settings.model_copy(update={"enrichment_adapter_mode": "live", "hunter_api_key": None})
    ctx = agent_context(clean_db, no_key, cipher, demo.tenant_id, demo.campaign_id)
    with pytest.raises(AdapterNotConfigured):
        providers_for(no_key, ctx)
    signals = settings.model_copy(update={"signals_adapter_mode": "live"})
    p = providers_for(signals, ctx)
    assert [b.source.name for b in p.sources] == ["contracts_finder.live", "find_a_tender.live"]
    assert "managed it" in p.sources[0].query and "-school" in p.sources[0].query
    assert p.enrichment.name == "enrichment.mock"  # paid providers stay mock unless their own mode says otherwise


def test_scheduled_hunt_runs_every_active_campaign(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    s = modes(settings, "live")
    hunter = Hunter()
    totals = hunt_active_campaigns(
        clean_db,
        s,
        cipher,
        now=datetime(2026, 10, 13, tzinfo=UTC),
        providers_factory=lambda st, ctx: live_providers(st, ctx, hunter),
    )
    assert totals == {"campaigns": 1, "discovered": 1, "processed": 1, "failed": 0} and hunter.calls == []
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        conn.execute(text("UPDATE campaigns SET status = 'paused'"))
    assert (
        hunt_active_campaigns(clean_db, s, cipher, providers_factory=lambda st, ctx: live_providers(st, ctx, hunter))[
            "campaigns"
        ]
        == 0
    )
