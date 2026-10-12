"""Transactional outbox relay: events written in the same transaction as state changes are dispatched later.
If the broker loses messages, undispatched rows are simply re-sent (consumers are idempotent)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.session import DbContext, transaction

Dispatcher = Callable[[str, dict[str, Any]], None]


def relay_batch(engine: Engine, dispatch: Dispatcher, limit: int = 100) -> int:
    with transaction(DbContext.system(), engine) as conn:
        rows = (
            conn.execute(
                text("""SELECT id, topic, payload FROM outbox WHERE dispatched_at IS NULL
              ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED"""),
                {"n": limit},
            )
            .mappings()
            .all()
        )
        done = []
        for r in rows:
            try:
                dispatch(r["topic"], dict(r["payload"]))
                done.append(r["id"])
            except Exception:
                conn.execute(text("UPDATE outbox SET attempts = attempts + 1 WHERE id = :id"), {"id": r["id"]})
        if done:
            conn.execute(text("UPDATE outbox SET dispatched_at = now() WHERE id = ANY(:ids)"), {"ids": done})
        return len(done)
