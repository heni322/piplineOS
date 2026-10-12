"""Idempotency for API requests and jobs with external side effects (Idempotency-Key header / job keys).

Same key + same request -> replay the stored response. Same key + different request -> 422.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.errors import ProblemError


def request_fingerprint(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def run_idempotent(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    scope: str,
    key: str | None,
    request: Any,
    fn: Callable[[], tuple[int, dict[str, Any]]],
) -> tuple[int, dict[str, Any]]:
    if not key or len(key) > 200:
        raise ProblemError(400, "Idempotency-Key header required (max 200 chars)")
    fp = request_fingerprint(request)
    # Lock the key row (insert-or-lock) so concurrent duplicates serialise.
    conn.execute(
        text("""INSERT INTO idempotency_keys (key, tenant_id, scope, request_hash) VALUES (:k, :t, :s, :h)
                         ON CONFLICT (tenant_id, scope, key) DO NOTHING"""),
        {"k": key, "t": tenant_id, "s": scope, "h": fp},
    )
    row = (
        conn.execute(
            text(
                "SELECT request_hash, status_code, response FROM idempotency_keys "
                "WHERE tenant_id = :t AND scope = :s AND key = :k FOR UPDATE"
            ),
            {"t": tenant_id, "s": scope, "k": key},
        )
        .mappings()
        .one()
    )
    if row["request_hash"] != fp:
        raise ProblemError(422, "Idempotency-Key reused with a different request")
    if row["status_code"] is not None:
        return int(row["status_code"]), dict(row["response"] or {})
    status, body = fn()
    conn.execute(
        text(
            "UPDATE idempotency_keys SET status_code = :c, response = CAST(:r AS jsonb) "
            "WHERE tenant_id = :t AND scope = :s AND key = :k"
        ),
        {"c": status, "r": json.dumps(body, default=str), "t": tenant_id, "s": scope, "k": key},
    )
    return status, body
