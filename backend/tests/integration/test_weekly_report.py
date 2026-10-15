"""Weekly results email: Mondays only, once per admin per week, demo numbers labelled, quiet weeks skipped,
dry-run until live, and each client sees only their own numbers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.conversations import run_conversations
from app.demo.seed import providers, seed_demo
from app.domain.reporting.weekly import compose, weekly_digest
from app.workflows.pipeline import agent_context, run_campaign
from tests.factories import make_tenant, make_tenant_user

pytestmark = pytest.mark.db


def test_quiet_weeks_send_nothing_and_demo_numbers_are_labelled() -> None:
    quiet = {"emails_sent": 0, "replied": 0, "meetings_booked": 0, "meetings_attended": 0}
    assert compose(quiet, date(2026, 10, 5), date(2026, 10, 11), "https://x") is None
    busy = {
        **quiet,
        "emails_sent": 20,
        "replied": 2,
        "positive_replies": 1,
        "meetings_booked": 1,
        "people_reached": 19,
        "qualified_meetings": 0,
        "bounces": 1,
        "complaints": 0,
        "data_mode": "dry_run",
        "per_100_reached": {"replies": 10.5, "positive_replies": 5.3},
    }
    subject, body = compose(busy, date(2026, 10, 5), date(2026, 10, 11), "https://app.example")  # type: ignore[misc]
    assert body.startswith("These are DEMO numbers") and "10.5 per 100" in body and "1 meeting booked" in subject


def test_mondays_only_once_per_admin_and_only_their_own_numbers(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    run_conversations(ctx, providers(), demo.operator_id)
    quiet = make_tenant(clean_db)  # an active client with nothing this week: no email
    make_tenant_user(clean_db, settings, cipher, quiet, email="quiet@client.example")
    with transaction(DbContext(role="operator"), clean_db) as conn:
        sent_days = (
            conn.execute(text("SELECT DISTINCT sent_at::date FROM messages WHERE sent_at IS NOT NULL")).scalars().all()
        )
    # The Monday after the week the demo emails were recorded in (they are sent at the real current time).
    last = max(sent_days)
    monday = datetime.combine(last + timedelta(days=7 - last.weekday()), datetime.min.time(), UTC)
    assert weekly_digest(clean_db, settings, cipher, now=monday - timedelta(hours=17)) == 0  # Sunday: nothing
    assert weekly_digest(clean_db, settings, cipher, now=monday.replace(hour=7)) >= 1
    weekly_digest(clean_db, settings, cipher, now=monday.replace(hour=13))  # a second run the same Monday
    with transaction(DbContext(role="operator"), clean_db) as conn:
        rows = conn.execute(
            text("SELECT tenant_id, idempotency_key FROM dry_run_log WHERE idempotency_key LIKE 'system:weekly:%'")
        ).all()
    assert rows and len(rows) == len({r.idempotency_key for r in rows})  # once per admin per week
    assert {r.tenant_id for r in rows} == {demo.tenant_id}  # the quiet client got nothing


def test_an_admin_who_turned_it_off_gets_nothing(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    run_conversations(ctx, providers(), demo.operator_id)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(text("UPDATE users SET weekly_report = false WHERE tenant_id = :t"), {"t": demo.tenant_id})
        last = max(conn.execute(text("SELECT sent_at::date FROM messages WHERE sent_at IS NOT NULL")).scalars().all())
    monday = datetime.combine(last + timedelta(days=7 - last.weekday()), datetime.min.time(), UTC)
    assert weekly_digest(clean_db, settings, cipher, now=monday.replace(hour=7)) == 0
