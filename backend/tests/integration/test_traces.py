"""The validation traces required in every phase report."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.traces import run_traces
from tests.conftest import CONFIG

pytestmark = pytest.mark.db


def test_required_sample_lead_traces(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    traces = {t["lead"].split(".")[0]: t for t in run_traces(clean_db, settings, CONFIG)}

    uk_corp = traces["1"]
    assert uk_corp["queue_decision"] == "allow" and uk_corp["send_status"] == "dry_run_sent"
    assert uk_corp["final_state"] == "contacted" and uk_corp["dry_run_log_rows"] == 1 and uk_corp["mode"] == "dry_run"

    sole = traces["2"]
    assert sole["subscriber_type"] == "individual"
    assert sole["queue_decision"] == "alternative_channel" and "INDIVIDUAL_SUBSCRIBER" in sole["queue_reasons"]
    assert (
        sole["final_state"] == "alternative_channel"
        and sole["dry_run_log_rows"] == 0
        and sole["alternative_tasks"] == 1
    )

    de = traces["3"]
    assert de["queue_decision"] == "alternative_channel" and "CONSENT_REQUIRED" in de["queue_reasons"]
    assert de["final_state"] == "alternative_channel" and de["dry_run_log_rows"] == 0

    supp = traces["4"]
    assert supp["queue_decision"] == "block" and supp["queue_reasons"] == ["SUPPRESSED_GLOBAL"]
    assert supp["final_state"] == "suppressed" and supp["dry_run_log_rows"] == 0

    late = traces["5"]
    assert late["queue_decision"] == "allow" and late["send_status"] == "blocked"
    assert late["send_reasons"] == ["SUPPRESSED_GLOBAL"] and late["final_state"] == "suppressed"

    with transaction(DbContext(role="operator"), clean_db) as conn:
        # Zero real side effects: nothing was ever marked 'sent' (only 'dry_run_sent'), nothing in a live mode.
        assert (
            conn.execute(
                text("SELECT count(*) FROM messages WHERE status = 'sent' OR mode_at_send <> 'dry_run'")
            ).scalar()
            == 0
        )
        assert conn.execute(text("SELECT count(*) FROM dry_run_log")).scalar() == 1
