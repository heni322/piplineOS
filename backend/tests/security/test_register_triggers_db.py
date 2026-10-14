"""Market-wide funding triggers end to end on PostgreSQL: the filing-stream ingest stores companies only, resumes
exactly where it stopped, and a client's hunt turns rows in its SIC codes into leads, skipping a company that is
being wound up before any AI is spent on it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from app.adapters.base import RateLimited
from app.adapters.companies_house.stream import StreamSlice, parse_event
from app.adapters.signals.register_filings import RegisterFilingSource
from app.agents.trigger_hunter.agent import SourceBinding
from app.db.session import DbContext, transaction
from app.demo.seed import providers, seed_demo
from app.domain.tenants.config import load_config, save_config
from app.workflows.pipeline import agent_context, run_campaign
from app.workflows.register_ingest import CURSOR, ingest_filings
from tests.contract.test_register_triggers import event

pytestmark = pytest.mark.db
SINCE = datetime(2026, 10, 1, tzinfo=UTC)

PROFILES: dict[str, dict[str, Any]] = {
    "01234567": {"company_name": "LAKESIDE NETWORKS LTD", "company_status": "active", "sic_codes": ["62020"]},
    "07654321": {"company_name": "SINKING SHIP IT LTD", "company_status": "active", "sic_codes": ["62020", "62090"]},
    "05555555": {"company_name": "HIGH STREET BAKERY LTD", "company_status": "active", "sic_codes": ["10710"]},
    "06666666": {"company_name": "GONE IT LTD", "company_status": "dissolved", "sic_codes": ["62020"]},
}


class FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self.lines, self.calls = lines, []  # type: ignore[var-annotated]

    def read(self, timepoint: int | None, *, max_events: int = 5000, max_seconds: float = 240.0) -> StreamSlice:
        self.calls.append(timepoint)
        out = StreamSlice([], timepoint)
        for line in self.lines:
            tp, ev = parse_event(line)
            if tp is None or (timepoint is not None and tp <= timepoint):
                continue
            out.read += 1
            out.last_timepoint = tp
            if ev:
                out.events.append(ev)
        return out


class FakeRegister:
    def __init__(self, limit_after: int | None = None) -> None:
        self.calls: list[str] = []
        self.limit_after = limit_after

    def profile(self, company_number: str) -> dict[str, Any] | None:
        if self.limit_after is not None and len(self.calls) >= self.limit_after:
            raise RateLimited("300")
        self.calls.append(company_number)
        return PROFILES.get(company_number)


LINES = [
    event(1, "01234567"),
    event(2, "07654321", ftype="MR01"),
    event(3, "05555555"),
    event(4, "06666666"),
    event(5, "09999999"),  # unknown to the register
    event(6, "01234567", ftype="AA"),  # not a trigger
]


def rows(engine):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(), engine) as conn:
        return conn.execute(
            text("SELECT company_number, filing_type, sic_codes FROM register_filing_events ORDER BY timepoint")
        ).all()


def cursor(engine):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(), engine) as conn:
        return conn.execute(text("SELECT timepoint FROM stream_cursors WHERE stream = :s"), {"s": CURSOR}).scalar()


def test_ingest_stores_trigger_companies_and_resumes_exactly(clean_db) -> None:  # type: ignore[no-untyped-def]
    stream = FakeStream(LINES)
    first = ingest_filings(clean_db, stream, FakeRegister(limit_after=2))  # rate limited after two lookups
    assert first.stored == 2 and first.deferred == 3
    assert cursor(clean_db) == 2  # just before the first event not handled
    second = ingest_filings(clean_db, stream, FakeRegister())
    assert stream.calls == [None, 2]
    assert second.stored == 2 and second.unknown_company == 1
    assert cursor(clean_db) == 6
    third = ingest_filings(clean_db, stream, FakeRegister())
    assert third.read == 0 and third.stored == 0
    assert [(r.company_number, r.filing_type) for r in rows(clean_db)] == [
        ("01234567", "SH01"),
        ("07654321", "MR01"),
        ("05555555", "SH01"),
        ("06666666", "SH01"),
    ]
    with transaction(DbContext.system(), clean_db) as conn:
        cols = set(
            conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'register_filing_events'")
            ).scalars()
        )
    assert not {"officer", "name_enc", "shareholder", "email"} & cols  # companies only


def test_replaying_the_stream_stores_nothing_twice(clean_db) -> None:  # type: ignore[no-untyped-def]
    ingest_filings(clean_db, FakeStream(LINES), FakeRegister())
    with transaction(DbContext.system(), clean_db) as conn:
        conn.execute(text("UPDATE stream_cursors SET timepoint = 0"))  # the app may not delete it
    again = ingest_filings(clean_db, FakeStream(LINES), FakeRegister())
    assert again.stored == 0 and again.duplicates == 4


class Insolvency:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def is_insolvent(self, company_number: str) -> str | None:
        self.asked.append(company_number)
        return "gazette:Petitions to Wind Up (Companies)" if company_number == "07654321" else None


def test_hunt_uses_the_clients_sic_codes_and_skips_insolvent_companies(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    ingest_filings(clean_db, FakeStream(LINES), FakeRegister())
    demo = seed_demo(clean_db, settings)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        _, cfg = load_config(conn, demo.tenant_id)
        cfg.icp.sic_codes = ["62020"]
        save_config(conn, demo.tenant_id, cfg)
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    p = providers()
    p.sources = [SourceBinding(RegisterFilingSource(clean_db, ctx.config.icp.sic_codes), "")]
    check = Insolvency()
    p.insolvency = check
    run = run_campaign(ctx, p, since=SINCE)

    # bakery: other SIC code; dissolved company: not active -> never fetched
    assert run.hunt.fetched == 2 and run.hunt.insolvent == 1
    assert check.asked == ["01234567", "07654321"]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        found = conn.execute(
            text(
                "SELECT c.name, s.signal_type, s.source_adapter FROM signals s JOIN companies c ON c.id = s.company_id"
            )
        ).all()
    assert [(f.name, f.signal_type, f.source_adapter) for f in found] == [
        ("LAKESIDE NETWORKS LTD", "funding", "companies_house.filings")
    ]
    assert RegisterFilingSource(clean_db, []).fetch("", SINCE) == []  # no SIC codes: off
