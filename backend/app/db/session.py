"""Database engine and tenant-scoped transactions (ADR-0003).

Every transaction sets ``app.tenant_id`` / ``app.role`` / ``app.user_id`` with ``set_config(..., true)``
(= SET LOCAL), so context never leaks across pooled connections. With no context, RLS policies
match nothing: the database fails closed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from app.settings import get_settings

Role = Literal["operator", "tenant_admin", "tenant_viewer", "system"]


@dataclass(frozen=True)
class DbContext:
    tenant_id: uuid.UUID | None = None
    role: Role | None = None
    user_id: uuid.UUID | None = None

    @staticmethod
    def system(tenant_id: uuid.UUID | None = None) -> DbContext:
        """Background jobs: act inside one tenant (or only on global tables when None)."""
        return DbContext(tenant_id=tenant_id, role="system")


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def apply_context(conn: Connection, ctx: DbContext) -> None:
    conn.execute(
        text(
            "SELECT set_config('app.tenant_id', :t, true), set_config('app.role', :r, true), "
            "set_config('app.user_id', :u, true)"
        ),
        {
            "t": str(ctx.tenant_id) if ctx.tenant_id else "",
            "r": ctx.role or "",
            "u": str(ctx.user_id) if ctx.user_id else "",
        },
    )


@contextmanager
def transaction(ctx: DbContext, engine: Engine | None = None) -> Iterator[Connection]:
    eng = engine or get_engine()
    with eng.begin() as conn:
        apply_context(conn, ctx)
        yield conn
