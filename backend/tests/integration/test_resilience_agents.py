"""Model and provider outages in the agents (ADR-0019): work waits for the dependency to come back; it is never
rejected, escalated to a person, or lost because of the outage. Safety rules (unsubscribe) work without the model."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.adapters.http import ProviderUnreachable
from app.demo.dataset import EXPECTED
from app.demo.seed import providers, seed_demo
from app.llm import client as llm_client
from app.llm.client import BackendResponse
from app.llm.simulator import SimulatedBackend
from app.workflows.pipeline import OUTAGE_BREAKER, agent_context, run_campaign
from tests.integration.test_pipeline import states
from tests.integration.test_replies import World, q

pytestmark = pytest.mark.db


class _WriterDown:
    """The simulator, except that the model is unreachable for the writer's calls."""

    def __init__(self) -> None:
        self.sim = SimulatedBackend()
        self.down = True

    def complete(self, **kw: Any) -> BackendResponse:
        if self.down and kw["json_schema"].get("title") in ("WriterDraft", "WriterCritique"):
            raise httpx.ConnectError("local model not running")
        return self.sim.complete(**kw)


def test_writer_outage_defers_drafts_and_the_next_run_completes_them(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    backend = _WriterDown()
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id, llm_backend=backend)
    run_campaign(ctx, providers())
    w = World(ctx, demo, None, {})  # type: ignore[arg-type]
    assert not q(w, "SELECT 1 FROM escalations WHERE reason = 'llm_failure'")  # nobody is asked to fix an outage
    assert not q(w, "SELECT 1 FROM messages WHERE kind = 'outreach'")
    waiting = {r.state for r in q(w, "SELECT state FROM leads")}
    assert "compliance_checked" in waiting and "pending_approval" not in waiting
    # the model is back (and its circuit has cooled down): the next run drafts exactly what was waiting
    backend.down = False
    llm_client._BREAKERS.clear()
    run_campaign(
        agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id, llm_backend=backend), providers()
    )
    assert states(clean_db, demo.tenant_id) == EXPECTED


def test_an_unreachable_provider_stops_the_run_after_a_few_leads_and_loses_nothing(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    p = providers()
    calls: list[str] = []

    def unreachable(*a: Any, **kw: Any) -> Any:
        calls.append("x")
        raise ProviderUnreachable("api.company-information.service.gov.uk unreachable")

    p.enrichment.find_people = unreachable  # type: ignore[method-assign]
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run = run_campaign(ctx, p)
    assert len(calls) == OUTAGE_BREAKER  # stopped calling the provider instead of once per lead
    assert run.outcomes == {"deferred": run.hunt.discovered}
    w = World(ctx, demo, None, {})  # type: ignore[arg-type]
    assert {r.state for r in q(w, "SELECT state FROM leads")} == {"discovered"}  # all kept, none rejected
    run_campaign(agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id), providers())
    assert states(clean_db, demo.tenant_id) == EXPECTED  # provider back: the same leads finish normally
