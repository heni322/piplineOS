"""Retention schedule: off until periods are set; erases only people nobody heard back from, past the period, and
exactly as an erasure request would; never anyone who replied, met or is still in flight."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.domain.dpr.retention import RetentionPolicy, run_retention
from tests.factories import enable, make_lead, make_operator, make_tenant
from tests.integration.test_gateway import IN_HOURS, approved_message, gw

pytestmark = pytest.mark.db
POLICY = RetentionPolicy(never_contacted_days=180, no_reply_days=365)


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    enable(clean_db, make_operator(clean_db, settings, cipher), "GB")
    return make_tenant(clean_db)


def _run(engine, t, policy, days, apply=True):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(t), engine) as conn:
        # Rows carry real now() timestamps, so the run is relative to the real clock (time-safe in any year).
        return run_retention(conn, t, policy, datetime.now(UTC) + timedelta(days=days), apply=apply)


def _erased(engine, t, prospect_id) -> bool:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(t), engine) as conn:
        r = conn.execute(text("SELECT anonymised_at, email_enc FROM prospects WHERE id = :p"), {"p": prospect_id}).one()
    return r.anonymised_at is not None and r.email_enc is None


def test_shipped_config_is_off_and_does_nothing(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import BACKEND

    policy = RetentionPolicy.load(Path(BACKEND).parent / "config")
    assert not policy.enabled
    lead = make_lead(clean_db, settings, cipher, world)
    assert _run(clean_db, world, policy, 10_000).erased == 0
    assert not _erased(clean_db, world, lead.prospect_id)


def test_never_contacted_people_are_erased_after_the_period(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, world)
    assert _run(clean_db, world, POLICY, 90).due == 0  # inside the period
    preview = _run(clean_db, world, POLICY, 200, apply=False)
    assert preview.due == 1 and preview.erased == 0 and not _erased(clean_db, world, lead.prospect_id)
    assert _run(clean_db, world, POLICY, 200).erased == 1
    assert _erased(clean_db, world, lead.prospect_id)
    with transaction(DbContext.system(world), clean_db) as conn:
        assert conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead.lead_id}).scalar() == "erased"
        assert conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'retention.erased'")).scalar() == 1
    assert _run(clean_db, world, POLICY, 400).due == 0  # never twice


def test_emailed_without_reply_counts_from_the_last_email(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, world)
    mid = approved_message(clean_db, settings, cipher, lead)
    assert gw(clean_db, settings, cipher).send_message(world, mid).status == "dry_run_sent"
    assert _run(clean_db, world, POLICY, 200).due == 0  # the never-contacted period does not apply
    assert _run(clean_db, world, POLICY, 400).erased == 1
    assert _erased(clean_db, world, lead.prospect_id)


def test_people_who_replied_or_are_in_flight_are_kept(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    replied = make_lead(clean_db, settings, cipher, world)
    mid = approved_message(clean_db, settings, cipher, replied)
    gw(clean_db, settings, cipher).send_message(world, mid)
    with transaction(DbContext.system(world), clean_db) as conn:
        conn.execute(
            text("INSERT INTO replies (tenant_id, lead_id, message_id, received_at) VALUES (:t, :l, :m, :r)"),
            {"t": world, "l": replied.lead_id, "m": mid, "r": IN_HOURS},
        )
    in_flight = make_lead(clean_db, settings, cipher, world)
    approved_message(clean_db, settings, cipher, in_flight)  # approved, waiting to be sent
    assert _run(clean_db, world, POLICY, 5_000).erased == 0
    assert not _erased(clean_db, world, replied.prospect_id)
    assert not _erased(clean_db, world, in_flight.prospect_id)


def test_a_person_is_erased_only_when_every_lead_for_them_is_past_the_period(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    old = make_lead(clean_db, settings, cipher, world)
    other = make_lead(clean_db, settings, cipher, world)
    with transaction(DbContext.system(world), clean_db) as conn:  # the same person, in a second campaign...
        conn.execute(
            text("UPDATE leads SET prospect_id = :p WHERE id = :l"), {"p": old.prospect_id, "l": other.lead_id}
        )
    mid = approved_message(clean_db, settings, cipher, other)
    assert mid  # ...who is about to be emailed there
    assert _run(clean_db, world, POLICY, 5_000).erased == 0
    assert not _erased(clean_db, world, old.prospect_id)


def test_periods_have_a_floor() -> None:
    with pytest.raises(ValueError):
        RetentionPolicy(never_contacted_days=7)
