"""Data quality per source in the report: every lead counted once, under the source of its signal, and the per-source
figures add up to the report's totals (leads found, people reached). Synthetic demo data, dry-run."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.conversations import _approve_all
from app.demo.seed import providers, seed_demo
from app.domain.reporting.metrics import metrics
from app.workflows.pipeline import agent_context, run_campaign, send_due

pytestmark = pytest.mark.db
IN_HOURS = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def test_per_source_figures_add_up_to_the_totals(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    run_campaign(agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id), providers())
    _approve_all(clean_db, cipher, demo.tenant_id, demo.operator_id)
    send_due(clean_db, settings, cipher, demo.tenant_id, now=IN_HOURS)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        m = metrics(conn, demo.tenant_id, date(2020, 1, 1), date(2030, 1, 1))
        sources = set(conn.execute(text("SELECT DISTINCT source_adapter FROM signals")).scalars())
        referred = conn.execute(text("SELECT count(*) FROM leads WHERE referred_from_lead_id IS NOT NULL")).scalar()
    rows = m["by_source"]
    assert rows and {r["source"] for r in rows} <= sources
    assert sum(r["leads"] for r in rows) == m["leads_discovered"] - referred
    assert sum(r["reached"] for r in rows) == m["people_reached"] > 0
    for r in rows:
        assert r["register_confirmed"] <= r["leads"] and r["with_email"] <= r["leads"]
        assert r["reached"] + r["bounced"] <= r["leads"] and r["positive"] <= r["replied"]
        assert r["email_rate"] == round(r["with_email"] / r["leads"], 3)
        assert r["reply_rate"] == (round(r["replied"] / r["reached"], 3) if r["reached"] else None)
    assert [r["leads"] for r in rows] == sorted((r["leads"] for r in rows), reverse=True)  # biggest source first
