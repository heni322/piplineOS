"""Global suppression list (the only cross-tenant data). Hash-only; never deleted; checked at queue AND send time."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.crypto import email_domain, suppression_hash
from app.domain.compliance.engine import SuppressionResult

Reason = Literal["unsubscribe", "objection", "erasure", "complaint", "bounce_hard", "manual", "data_protection_request"]


@dataclass(frozen=True)
class RecipientHashes:
    email: bytes
    domain: bytes

    @staticmethod
    def for_email(key: bytes, email: str) -> RecipientHashes:
        return RecipientHashes(
            suppression_hash(key, email, "email"), suppression_hash(key, email_domain(email), "domain")
        )


def check(conn: Connection, tenant_id: uuid.UUID, hashes: RecipientHashes) -> SuppressionResult:
    rows = conn.execute(
        text("SELECT scope, tenant_id FROM suppression_list WHERE identifier_hash IN (:e, :d)"),
        {"e": hashes.email, "d": hashes.domain},
    ).all()
    return SuppressionResult(
        global_hit=any(r.scope == "global" for r in rows),
        tenant_hit=any(r.scope == "tenant" and r.tenant_id == tenant_id for r in rows),
        checked_at=datetime.now(UTC),
    )


def suppress_email(
    conn: Connection,
    hashes: RecipientHashes,
    reason: Reason,
    *,
    scope: Literal["global", "tenant"] = "global",
    tenant_id: uuid.UUID | None = None,
    source_ref: str | None = None,
) -> bool:
    """Idempotent. Returns True if a new entry was written. Objections/unsubscribes are GLOBAL by policy."""
    if scope == "tenant" and tenant_id is None:
        raise ValueError("tenant scope requires tenant_id")
    index = (
        "(identifier_hash) WHERE scope = 'global'"
        if scope == "global"
        else "(tenant_id, identifier_hash) WHERE scope = 'tenant'"
    )
    res = conn.execute(
        text(
            "INSERT INTO suppression_list (identifier_hash, identifier_type, scope, tenant_id, reason, source_ref) "  # noqa: S608
            f"VALUES (:h, 'email', :s, :t, :r, :src) ON CONFLICT {index} DO NOTHING"
        ),
        {"h": hashes.email, "s": scope, "t": tenant_id if scope == "tenant" else None, "r": reason, "src": source_ref},
    )
    return bool(res.rowcount)


def suppress_domain(conn: Connection, domain_hash: bytes, reason: Reason, *, source_ref: str | None = None) -> bool:
    res = conn.execute(
        text(
            "INSERT INTO suppression_list (identifier_hash, identifier_type, scope, reason, source_ref) "
            "VALUES (:h, 'domain', 'global', :r, :src) ON CONFLICT (identifier_hash) WHERE scope = 'global' DO NOTHING"
        ),
        {"h": domain_hash, "r": reason, "src": source_ref},
    )
    return bool(res.rowcount)
