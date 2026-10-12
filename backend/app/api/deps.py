"""Request-scoped dependencies: current user from the session cookie, CSRF, permissions, DB context."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.security import Permission, has_permission, hash_session_token
from app.db.session import DbContext, Role, get_engine, transaction
from app.settings import Settings, get_settings

SESSION_COOKIE = "pos_session"
CSRF_COOKIE = "pos_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: Role
    mfa_verified: bool

    def db_context(self, tenant_id: uuid.UUID | None = None) -> DbContext:
        """Operators may act inside a chosen tenant; tenant users are always pinned to their own tenant."""
        if self.role == "operator":
            return DbContext(tenant_id=tenant_id, role="operator", user_id=self.id)
        return DbContext(tenant_id=self.tenant_id, role=self.role, user_id=self.id)


def settings_dep() -> Settings:
    return get_settings()


_cipher: PiiCipher | None = None


def cipher_dep(settings: Annotated[Settings, Depends(settings_dep)]) -> PiiCipher:
    global _cipher  # noqa: PLW0603 - process-wide key cache
    if _cipher is None:
        _cipher = PiiCipher(settings.key("pii_master_key"))
    return _cipher


def current_user(request: Request, settings: Annotated[Settings, Depends(settings_dep)]) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise ProblemError(401, "Not authenticated")
    if request.method not in SAFE_METHODS:
        header, cookie = request.headers.get(CSRF_HEADER, ""), request.cookies.get(CSRF_COOKIE, "")
        if not header or not cookie or not secrets.compare_digest(header, cookie):
            raise ProblemError(403, "CSRF token missing or invalid")
    now = datetime.now(UTC)
    with transaction(DbContext(), get_engine()) as conn:
        s = (
            conn.execute(
                text(
                    "SELECT user_id, idle_expires_at, expires_at, mfa_verified_at, revoked_at FROM sessions "
                    "WHERE id = :id"
                ),
                {"id": hash_session_token(token)},
            )
            .mappings()
            .first()
        )
        if s is None or s["revoked_at"] or s["expires_at"] <= now or s["idle_expires_at"] <= now:
            raise ProblemError(401, "Session expired")
        u = conn.execute(text("SELECT * FROM auth_get_user(:id)"), {"id": s["user_id"]}).mappings().first()
        if u is None or u["status"] != "active":
            raise ProblemError(401, "Account disabled")
        if u["role"] == "operator" and not s["mfa_verified_at"]:
            raise ProblemError(401, "MFA required")
        idle = timedelta(hours=settings.session_idle_hours_tenant)
        conn.execute(
            text("UPDATE sessions SET last_seen_at = :n, idle_expires_at = LEAST(expires_at, :i) WHERE id = :id"),
            {"n": now, "i": now + idle, "id": hash_session_token(token)},
        )
    return CurrentUser(u["id"], u["tenant_id"], u["role"], bool(s["mfa_verified_at"]))


def require(permission: Permission) -> Callable[[CurrentUser], CurrentUser]:
    def _dep(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if not has_permission(user.role, permission):
            raise ProblemError(403, "Forbidden", f"missing permission: {permission.value}")
        return user

    return _dep


def tenant_tx(user: CurrentUser, tenant_id: uuid.UUID | None = None) -> Iterator[Connection]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        yield conn
