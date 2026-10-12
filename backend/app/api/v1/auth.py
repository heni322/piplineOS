from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from app.adapters.base import TokenBucket
from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, CurrentUser, cipher_dep, current_user, settings_dep
from app.core.audit import write_audit
from app.core.crypto import PiiCipher, blind_index
from app.core.errors import ProblemError
from app.core.security import (
    burn_password_check,
    hash_session_token,
    new_session_token,
    verify_password,
    verify_totp,
)
from app.db.session import DbContext, get_engine, transaction
from app.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])
_ip_buckets: dict[str, TokenBucket] = {}
GENERIC = "Invalid credentials"


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, max_length=10)


class MeOut(BaseModel):
    user_id: str
    tenant_id: str | None
    role: str
    mfa_verified: bool


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=MeOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(settings_dep)],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> MeOut:
    bucket = _ip_buckets.setdefault(_client_ip(request), TokenBucket(rate_per_sec=0.2, capacity=10))
    if not bucket.try_acquire():
        raise ProblemError(429, "Too many login attempts")
    engine = get_engine()
    bidx = blind_index(settings.key("blind_index_key"), body.email, None)
    with transaction(DbContext(), engine) as conn:
        u = conn.execute(text("SELECT * FROM auth_find_user(:b)"), {"b": bidx}).mappings().first()
        if u is None:
            burn_password_check()
            raise ProblemError(401, GENERIC)
        locked = u["locked_until"] is not None and u["locked_until"] > datetime.now(UTC)
        ok = verify_password(u["password_hash"], body.password) and not locked and u["status"] == "active"
        mfa_ok = False
        if ok and (u["role"] == "operator" or u["mfa_enabled"]):
            if not u["mfa_enabled"] or not u["mfa_secret_enc"]:
                ok = False  # operators must have MFA enrolled
            else:
                secret = cipher.decrypt(conn, None, "users.mfa_secret", u["mfa_secret_enc"])
                mfa_ok = verify_totp(secret, body.totp_code or "")
                ok = mfa_ok
        conn.execute(text("SELECT auth_record_login(:id, :ok)"), {"id": u["id"], "ok": ok})
        write_audit(
            conn,
            action="auth.login.success" if ok else "auth.login.failure",
            actor_type="user",
            actor_id=str(u["id"]),
            tenant_id=None,
            after={"ip_country": request.headers.get("x-client-country")},
        )
    # The failure counter and audit entry must be COMMITTED before rejecting (raising inside the transaction
    # would roll them back and the account would never lock).
    if not ok:
        raise ProblemError(401, GENERIC)
    with transaction(DbContext(), engine) as conn:
        token, token_hash = new_session_token()
        now = datetime.now(UTC)
        absolute = (
            timedelta(hours=settings.session_absolute_hours_operator) if u["role"] == "operator" else timedelta(days=7)
        )
        idle = min(absolute, timedelta(hours=settings.session_idle_hours_tenant))
        conn.execute(
            text("""INSERT INTO sessions (id, user_id, idle_expires_at, expires_at, mfa_verified_at, ip_country)
              VALUES (:id, :u, :i, :e, :m, :c)"""),
            {
                "id": token_hash,
                "u": u["id"],
                "i": now + idle,
                "e": now + absolute,
                "m": now if mfa_ok else None,
                "c": request.headers.get("x-client-country"),
            },
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=int(absolute.total_seconds()),
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(24),
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=int(absolute.total_seconds()),
        path="/",
    )
    return MeOut(
        user_id=str(u["id"]),
        tenant_id=str(u["tenant_id"]) if u["tenant_id"] else None,
        role=u["role"],
        mfa_verified=mfa_ok,
    )


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: Annotated[CurrentUser, Depends(current_user)]) -> Response:
    token = request.cookies.get(SESSION_COOKIE, "")
    with transaction(DbContext(user_id=user.id), get_engine()) as conn:
        conn.execute(text("UPDATE sessions SET revoked_at = now() WHERE id = :id"), {"id": hash_session_token(token)})
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut)
def me(user: Annotated[CurrentUser, Depends(current_user)]) -> MeOut:
    return MeOut(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id) if user.tenant_id else None,
        role=user.role,
        mfa_verified=user.mfa_verified,
    )
