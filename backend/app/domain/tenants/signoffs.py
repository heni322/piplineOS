"""What the client agreed to: qualification criteria and pricing, as immutable snapshots with a content hash.
Billing checks the hash of the criteria a lead was qualified against, so editing criteria after signing can't make
earlier meetings billable under new terms."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit
from app.domain.tenants.config import TenantConfig, load_config

Kind = Literal["qualification_criteria", "pricing"]


def terms_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def terms_of(cfg: TenantConfig, kind: Kind) -> dict[str, Any] | None:
    block = cfg.qualification if kind == "qualification_criteria" else cfg.pricing
    return block.model_dump(mode="json") if block else None


class NothingToSign(Exception):
    pass


def sign(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    kind: Kind,
    user_id: uuid.UUID,
    method: Literal["portal", "recorded_by_operator"],
    evidence_ref: str | None = None,
) -> uuid.UUID:
    try:
        config_id, cfg = load_config(conn, tenant_id)
    except LookupError as exc:
        raise NothingToSign(kind) from exc
    content = terms_of(cfg, kind)
    if content is None:
        raise NothingToSign(kind)
    sid: uuid.UUID = conn.execute(
        text("""INSERT INTO tenant_signoffs (tenant_id, kind, config_id, content, content_hash, signed_by, method,
          evidence_ref) VALUES (:t, :k, :c, CAST(:j AS jsonb), :h, :u, :m, :e) RETURNING id"""),
        {
            "t": tenant_id,
            "k": kind,
            "c": config_id,
            "j": json.dumps(content),
            "h": terms_hash(content),
            "u": user_id,
            "m": method,
            "e": evidence_ref,
        },
    ).scalar_one()
    write_audit(
        conn,
        action=f"terms.signed.{kind}",
        actor_type="user",
        actor_id=str(user_id),
        tenant_id=tenant_id,
        entity_type="tenant_signoff",
        entity_id=sid,
        after={"method": method, "hash": terms_hash(content)},
    )
    return sid


def signed(conn: Connection, tenant_id: uuid.UUID, kind: Kind) -> list[tuple[str, datetime]]:
    return [
        (r.content_hash, r.signed_at)
        for r in conn.execute(
            text("SELECT content_hash, signed_at FROM tenant_signoffs WHERE tenant_id = :t AND kind = :k"),
            {"t": tenant_id, "k": kind},
        )
    ]


def current_is_signed(conn: Connection, tenant_id: uuid.UUID, kind: Kind) -> bool:
    try:
        _, cfg = load_config(conn, tenant_id)
    except LookupError:  # not configured yet: there is nothing to have signed
        return False
    content = terms_of(cfg, kind)
    return content is not None and any(h == terms_hash(content) for h, _ in signed(conn, tenant_id, kind))
