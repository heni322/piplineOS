"""Market-wide trigger ingest: new share allotments (SH01) and new charges (MR01) from the Companies House filing
stream, stored once for every client in `register_filing_events` (companies only, no personal data).

Runs every few minutes. Each run reads one bounded slice of the stream from the saved timepoint, looks up each
trigger company's profile (name, status, SIC codes) and saves the cursor only up to the last event fully handled:
a rate limit or the per-run lookup cap stops early and the next run resumes there, so nothing is lost or doubled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.base import RateLimited
from app.adapters.companies_house.stream import FilingEvent, StreamSlice
from app.core.logging import get_logger
from app.db.session import DbContext, transaction

log = get_logger(__name__)
CURSOR = "companies_house.filings"


class Stream(Protocol):
    def read(self, timepoint: int | None, *, max_events: int = ..., max_seconds: float = ...) -> StreamSlice: ...


class Profiles(Protocol):
    def profile(self, company_number: str) -> dict[str, Any] | None: ...


@dataclass
class IngestStats:
    read: int = 0
    triggers: int = 0
    stored: int = 0
    duplicates: int = 0
    unknown_company: int = 0
    deferred: int = 0
    reset: bool = False


def _cursor(engine: Engine) -> int | None:
    with transaction(DbContext.system(), engine) as conn:
        tp = conn.execute(text("SELECT timepoint FROM stream_cursors WHERE stream = :s"), {"s": CURSOR}).scalar()
    return int(tp) if tp is not None else None


def _save_cursor(engine: Engine, timepoint: int) -> None:
    with transaction(DbContext.system(), engine) as conn:
        conn.execute(
            text("""INSERT INTO stream_cursors (stream, timepoint) VALUES (:s, :t)
              ON CONFLICT (stream) DO UPDATE SET timepoint = excluded.timepoint, updated_at = now()"""),
            {"s": CURSOR, "t": timepoint},
        )


def _store(engine: Engine, ev: FilingEvent, prof: dict[str, Any]) -> bool:
    with transaction(DbContext.system(), engine) as conn:
        row = conn.execute(
            text("""INSERT INTO register_filing_events (company_number, company_name, company_status, sic_codes,
              filing_type, filed_on, capital, transaction_ref, timepoint)
              VALUES (:n, :name, :st, :sic, :ft, :fo, :cap, :ref, :tp)
              ON CONFLICT (company_number, transaction_ref) DO NOTHING RETURNING id"""),
            {
                "n": ev.company_number,
                "name": str(prof.get("company_name") or ev.company_number)[:300],
                "st": str(prof.get("company_status") or "unknown"),
                "sic": [str(c) for c in prof.get("sic_codes") or [] if str(c).isdigit()],
                "ft": ev.filing_type,
                "fo": ev.filed_on,
                "cap": ev.capital,
                "ref": ev.transaction_ref,
                "tp": ev.timepoint,
            },
        ).scalar()
    return row is not None


def ingest_filings(
    engine: Engine,
    stream: Stream,
    profiles: Profiles,
    *,
    max_events: int = 5000,
    max_seconds: float = 240.0,
    max_lookups: int = 300,
) -> IngestStats:
    stats = IngestStats()
    start = _cursor(engine)
    sl = stream.read(start, max_events=max_events, max_seconds=max_seconds)
    stats.read, stats.triggers, stats.reset = sl.read, len(sl.events), sl.reset
    if sl.reset:
        log.warning("filing_stream_reset", previous_timepoint=start)  # a gap: the job was off for too long
    resume_at = sl.last_timepoint  # where the next run starts if every trigger is handled
    seen: dict[str, dict[str, Any] | None] = {}
    lookups = 0
    for i, ev in enumerate(sl.events):
        if ev.company_number not in seen:
            if lookups >= max_lookups:
                resume_at, stats.deferred = ev.timepoint - 1, len(sl.events) - i
                break
            try:
                seen[ev.company_number] = profiles.profile(ev.company_number)
            except RateLimited:
                resume_at, stats.deferred = ev.timepoint - 1, len(sl.events) - i
                break
            lookups += 1
        prof = seen[ev.company_number]
        if prof is None:
            stats.unknown_company += 1
            continue
        if _store(engine, ev, prof):
            stats.stored += 1
        else:
            stats.duplicates += 1
    if resume_at is not None and resume_at != start:
        _save_cursor(engine, resume_at)
    log.info("filing_stream_ingest", **stats.__dict__)
    return stats
