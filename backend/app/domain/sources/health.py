"""Source health: record each run of a signal source, and summarise the last 24 hours for the operator and for
monitoring. A source that keeps failing (a changed API format, a renamed job board, a lapsed key) shows up here
instead of silently finding nothing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.adapters.http import ProviderError
from app.core.logging import get_logger
from app.db.session import DbContext, transaction

log = get_logger(__name__)


def describe_error(exc: BaseException) -> str:
    """The error type, and the HTTP status for provider errors. Never the message body (it can echo anything)."""
    if isinstance(exc, ProviderError):
        return f"{type(exc).__name__}: HTTP {exc.status}"
    return type(exc).__name__


def record_run(
    engine: Engine,
    source: str,
    *,
    started_at: datetime,
    outcome: str,
    fetched: int = 0,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Best effort: a failure to record must never break the hunt itself."""
    import json

    try:
        with transaction(DbContext.system(), engine) as conn:
            conn.execute(
                text("""INSERT INTO source_runs (source, started_at, outcome, fetched, error, detail)
                  VALUES (:s, :st, :o, :f, :e, CAST(:d AS jsonb))"""),
                {
                    "s": source[:100],
                    "st": started_at,
                    "o": outcome,
                    "f": max(0, fetched),
                    "e": error[:200] if error else None,
                    "d": json.dumps(detail or {}),
                },
            )
    except Exception as exc:
        log.warning("source_run_not_recorded", source=source, error=type(exc).__name__)


@dataclass(frozen=True)
class SourceHealth:
    source: str
    runs_24h: int
    failures_24h: int
    fetched_24h: int
    last_run_at: datetime
    last_outcome: str
    last_error: str | None
    last_success_at: datetime | None
    status: str  # healthy | degraded | failing


def summary(conn: Connection) -> list[SourceHealth]:
    rows = conn.execute(
        text("""
        WITH recent AS (SELECT * FROM source_runs WHERE finished_at > now() - interval '24 hours'),
        last AS (SELECT DISTINCT ON (source) source, finished_at, outcome, error FROM source_runs
                 ORDER BY source, finished_at DESC),
        ok AS (SELECT source, max(finished_at) AS at FROM source_runs WHERE outcome IN ('ok', 'partial')
               GROUP BY source)
        SELECT l.source, l.finished_at, l.outcome, l.error, ok.at AS last_success,
          count(r.id) AS runs, count(r.id) FILTER (WHERE r.outcome = 'failed') AS failures,
          coalesce(sum(r.fetched), 0) AS fetched
        FROM last l LEFT JOIN recent r ON r.source = l.source LEFT JOIN ok ON ok.source = l.source
        GROUP BY l.source, l.finished_at, l.outcome, l.error, ok.at ORDER BY l.source""")
    ).all()
    out = []
    for r in rows:
        if r.outcome == "failed" and (r.last_success is None or r.failures >= 3):
            status = "failing"
        elif r.outcome in ("failed", "partial", "rate_limited") or r.failures:
            status = "degraded"
        else:
            status = "healthy"
        out.append(
            SourceHealth(
                r.source, r.runs, r.failures, int(r.fetched), r.finished_at, r.outcome, r.error, r.last_success, status
            )
        )
    return out
