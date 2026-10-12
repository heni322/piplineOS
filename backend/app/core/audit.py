"""Audit log writer. Every state transition and privileged action is recorded (append-only table)."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.logging import correlation_id_var, scrub_value

ActorType = Literal["user", "system", "agent"]


def write_audit(
    conn: Connection,
    *,
    action: str,
    actor_type: ActorType,
    actor_id: str | None,
    tenant_id: uuid.UUID | None,
    entity_type: str | None = None,
    entity_id: Any = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, entity_type, entity_id, before, after,"
            " correlation_id) VALUES (:t, :at, :aid, :a, :et, :eid, CAST(:b AS jsonb), CAST(:af AS jsonb), :c)"
        ),
        {
            "t": tenant_id,
            "at": actor_type,
            "aid": actor_id,
            "a": action,
            "et": entity_type,
            "eid": str(entity_id) if entity_id is not None else None,
            # audit rows must stay PII-free: scrub defensively
            "b": json.dumps(scrub_value(before), default=str) if before is not None else None,
            "af": json.dumps(scrub_value(after), default=str) if after is not None else None,
            "c": correlation_id or correlation_id_var.get(),
        },
    )
