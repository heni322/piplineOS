"""Transactional outbox relay: events written in the same transaction as state changes are dispatched later.
If the broker loses messages, undispatched rows are simply re-sent (consumers are idempotent).

A row that keeps failing backs off exponentially and, after MAX_ATTEMPTS, is dead-lettered: left in place for a
human (counted by the pipelineos_outbox_dead metric and alert) instead of being retried forever. Healthy events
are never stuck behind it, because rows that are waiting out a backoff are not selected (ADR-0019)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.logging import get_logger
from app.db.session import DbContext, transaction

Dispatcher = Callable[[str, dict[str, Any]], None]
MAX_ATTEMPTS = 10  # with the backoff below: about half an hour of retries, then a human is asked to look
log = get_logger(__name__)


def relay_batch(engine: Engine, dispatch: Dispatcher, limit: int = 100) -> int:
    with transaction(DbContext.system(), engine) as conn:
        rows = (
            conn.execute(
                text("""SELECT id, topic, payload, attempts FROM outbox WHERE dispatched_at IS NULL
              AND attempts < :max AND (next_attempt_at IS NULL OR next_attempt_at <= now())
              ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED"""),
                {"n": limit, "max": MAX_ATTEMPTS},
            )
            .mappings()
            .all()
        )
        done = []
        for r in rows:
            try:
                dispatch(r["topic"], dict(r["payload"]))
                done.append(r["id"])
            except Exception as exc:
                conn.execute(
                    # 2^attempts seconds, capped at 2 hours: a broker blip retries within seconds, an outage slowly
                    text("""UPDATE outbox SET attempts = attempts + 1,
                      next_attempt_at = now() + make_interval(secs => least(power(2, attempts + 1), 7200))
                      WHERE id = :id"""),
                    {"id": r["id"]},
                )
                if r["attempts"] + 1 >= MAX_ATTEMPTS:
                    log.error("outbox_dead_lettered", outbox_id=str(r["id"]), topic=r["topic"])
                else:
                    log.warning("outbox_dispatch_failed", topic=r["topic"], error=type(exc).__name__)
        if done:
            conn.execute(text("UPDATE outbox SET dispatched_at = now() WHERE id = ANY(:ids)"), {"ids": done})
        return len(done)
