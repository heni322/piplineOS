"""Subject-line A/B test: off by default; when on, each lead gets a stable variant on its first email only, and the
report compares variants per 100 people reached without declaring a winner on too little data."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.conversations import _approve_all
from app.demo.dataset import DEMO_CONFIG
from app.demo.seed import providers, seed_demo
from app.domain.reporting.metrics import metrics
from app.domain.tenants.config import save_config
from app.workflows.pipeline import agent_context, run_campaign, send_due

pytestmark = pytest.mark.db
IN_HOURS = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def _run(clean_db, settings, cipher, variants: int):  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    if variants > 1:
        cfg = DEMO_CONFIG.model_copy(
            update={"sequence": DEMO_CONFIG.sequence.model_copy(update={"subject_variants": variants})}
        )
        with transaction(DbContext(role="operator"), clean_db) as conn:
            save_config(conn, demo.tenant_id, cfg, None)
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    return demo


def test_off_by_default(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = _run(clean_db, settings, cipher, 1)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM messages WHERE subject_variant IS NOT NULL")).scalar() == 0


def test_variants_are_stable_per_lead_and_reported(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = _run(clean_db, settings, cipher, 2)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        rows = conn.execute(
            text("SELECT lead_id, sequence_step, subject_variant FROM messages WHERE kind = 'outreach'")
        ).all()
        assert rows
        for r in rows:
            assert r.subject_variant == (r.lead_id.int % 2 if r.sequence_step == 0 else None)
    _approve_all(clean_db, cipher, demo.tenant_id, demo.operator_id)
    send_due(clean_db, settings, cipher, demo.tenant_id, now=IN_HOURS)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        m = metrics(conn, demo.tenant_id, datetime(2020, 1, 1).date(), datetime(2030, 1, 1).date())
    test = m["subject_test"]
    assert test is not None and test["verdict"] == "not_enough_data" and test["leader"] is None
    assert sum(v["reached"] for v in test["variants"]) == m["people_reached"]
