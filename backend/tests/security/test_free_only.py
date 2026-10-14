"""FREE_ONLY end to end: a public award notice -> the company's directors from the free Companies House register ->
a prospect with no email address. No paid provider is built or called, no address is guessed, and nothing is sent."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.base import AdapterNotConfigured
from app.adapters.companies_house.base import RegistryCompany
from app.adapters.companies_house.mock import MockCompaniesHouse
from app.adapters.companies_house.officers import CompaniesHouseOfficers
from app.adapters.registry import providers_for, signal_query
from app.adapters.signals.ocds import contracts_finder
from app.agents.trigger_hunter.agent import SourceBinding
from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.workflows.pipeline import agent_context, run_campaign
from tests.security.test_paid_gate import COMPANY, NOTICES, NUMBER

pytestmark = pytest.mark.db
SINCE = datetime(2026, 10, 1, tzinfo=UTC)

OFFICERS = {
    "items": [
        {
            "name": "REYES, Dana",
            "officer_role": "director",
            "appointed_on": "2018-02-01",
            "occupation": "Operations Director",
            "country_of_residence": "England",
            "date_of_birth": {"month": 1, "year": 1979},
            "links": {"self": "/company/01234567/appointments/abc"},
        }
    ]
}


def free(settings):  # type: ignore[no-untyped-def]
    return settings.model_copy(
        update={
            "free_only": True,
            "enrichment_adapter_mode": "live",
            "verifier_adapter_mode": "live",
            "companies_house_api_key": SecretStr("free-key"),
            "hunter_api_key": SecretStr("paid-key-never-used"),
        }
    )


def test_free_only_builds_no_paid_provider(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    s = free(settings)
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    p = providers_for(s, ctx)
    assert p.enrichment.name == "companies_house.officers" and p.verifier.name == "verifier.none"
    assert p.verifier.verify("anyone@lakeside-net.example") == "unknown"  # never reported valid
    no_key = s.model_copy(update={"companies_house_api_key": None})
    with pytest.raises(AdapterNotConfigured):
        providers_for(no_key, agent_context(clean_db, no_key, cipher, demo.tenant_id, demo.campaign_id))


def test_free_only_pipeline_finds_the_director_without_an_email(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    s = free(settings)
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    calls: list[httpx.Request] = []

    def ch(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == f"/company/{NUMBER}/officers":
            return httpx.Response(200, json=OFFICERS)
        return httpx.Response(404, json={})

    p = providers_for(s, ctx)
    p.sources = [
        SourceBinding(
            contracts_finder(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=NOTICES))),
            signal_query(ctx.config),
        )
    ]
    p.enrichment = CompaniesHouseOfficers("free-key", transport=httpx.MockTransport(ch))
    p.registry = MockCompaniesHouse({COMPANY: RegistryCompany(NUMBER, COMPANY, "ltd", "active", "GB")})
    run = run_campaign(ctx, p, since=SINCE)

    assert run.hunt.discovered == 1
    assert [c.url.path for c in calls] == [f"/company/{NUMBER}/officers"]  # by the notice's register number
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        lead = conn.execute(
            text(
                "SELECT l.id, l.state, l.prospect_id FROM leads l JOIN companies c ON c.id = l.company_id "
                "WHERE c.company_number = :n"
            ),
            {"n": NUMBER},
        ).one()
        assert lead.prospect_id is not None and lead.state == "alternative_channel"
        task = conn.execute(
            text("SELECT channel, instructions FROM alternative_channel_tasks WHERE lead_id = :l"), {"l": lead.id}
        ).one()
        assert task.channel == "published_contact" and f"companies_house:officers:{NUMBER}" in task.instructions
        assert "No automated messaging" in task.instructions
        pr = conn.execute(
            text("SELECT email_enc, email_bidx, title, email_verification FROM prospects WHERE id = :p"),
            {"p": lead.prospect_id},
        ).one()
        assert pr.email_enc is None and pr.email_bidx is None  # no address bought or guessed
        assert pr.title == "Operations Director" and pr.email_verification == "unknown"
        country = conn.execute(text("SELECT country_code FROM prospects WHERE id = :p"), {"p": lead.prospect_id})
        assert country.scalar_one() == "GB"
        sources = set(
            conn.execute(
                text("SELECT data_source FROM prospect_field_sources WHERE prospect_id = :p"), {"p": lead.prospect_id}
            ).scalars()
        )
        assert f"companies_house:officers:{NUMBER}" in sources
        sent = conn.execute(text("SELECT count(*) FROM messages WHERE status = 'sent'")).scalar_one()
        paid = conn.execute(text("SELECT count(*) FROM dry_run_log WHERE effect_type = 'paid_api_call'")).scalar_one()
    assert sent == 0 and paid == 0


def test_a_director_outside_the_buyer_personas_is_still_rejected(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    """Only "no address" becomes a human task; any other reason (here: role not relevant) still blocks."""
    demo = seed_demo(clean_db, settings)
    s = free(settings)
    ctx = agent_context(clean_db, s, cipher, demo.tenant_id, demo.campaign_id)
    officers = {"items": [dict(OFFICERS["items"][0], occupation="Company Secretary")]}
    p = providers_for(s, ctx)
    p.sources = [
        SourceBinding(
            contracts_finder(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=NOTICES))),
            signal_query(ctx.config),
        )
    ]
    p.enrichment = CompaniesHouseOfficers(
        "free-key", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=officers))
    )
    p.registry = MockCompaniesHouse({COMPANY: RegistryCompany(NUMBER, COMPANY, "ltd", "active", "GB")})
    run_campaign(ctx, p, since=SINCE)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        states = conn.execute(text("SELECT state FROM leads")).scalars().all()
        tasks = conn.execute(text("SELECT count(*) FROM alternative_channel_tasks")).scalar_one()
    assert states == ["rejected_by_compliance"] and tasks == 0
