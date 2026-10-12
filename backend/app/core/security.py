"""Passwords (Argon2id), TOTP, session tokens and the role -> permission matrix (ADR-0008)."""

from __future__ import annotations

import hashlib
import secrets
from enum import StrEnum

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()  # argon2id with library defaults (RFC 9106 recommended parameters)
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _ph.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _ph.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


_DUMMY_HASH = _ph.hash("dummy-password-for-timing")


def burn_password_check() -> None:
    """Run a verification against a dummy hash so unknown accounts take as long as known ones."""
    verify_password(_DUMMY_HASH, "not-the-password")


def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    return bool(code) and pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def new_session_token() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).digest()


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class Permission(StrEnum):
    VIEW_TENANT = "view_tenant"
    VIEW_LEADS = "view_leads"
    UNMASK_PII = "unmask_pii"
    APPROVE_MESSAGES = "approve_messages"
    MANAGE_TENANT_USERS = "manage_tenant_users"
    CONFIRM_ATTENDANCE = "confirm_attendance"
    RAISE_DISPUTE = "raise_dispute"
    VIEW_ALL_TENANTS = "view_all_tenants"
    MANAGE_COMPLIANCE = "manage_compliance"
    SIGN_OFF_GOLIVE = "sign_off_golive"
    VIEW_COMPLIANCE_LOG = "view_compliance_log"


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "operator": frozenset(Permission),
    "tenant_admin": frozenset(
        {
            Permission.VIEW_TENANT,
            Permission.VIEW_LEADS,
            Permission.MANAGE_TENANT_USERS,
            Permission.CONFIRM_ATTENDANCE,
            Permission.RAISE_DISPUTE,
            Permission.UNMASK_PII,
            Permission.VIEW_COMPLIANCE_LOG,
        }
    ),
    "tenant_viewer": frozenset({Permission.VIEW_TENANT, Permission.VIEW_LEADS}),
}


def has_permission(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
