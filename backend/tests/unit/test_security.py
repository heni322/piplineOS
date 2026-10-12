from __future__ import annotations

import pyotp
import pytest

from app.core.security import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
    hash_password,
    new_session_token,
    verify_password,
    verify_totp,
)


def test_password_hashing_uses_argon2id() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery")
    assert not verify_password(h, "wrong")
    assert not verify_password("not-a-hash", "x")


def test_short_passwords_rejected() -> None:
    with pytest.raises(ValueError):
        hash_password("short")


def test_totp() -> None:
    secret = pyotp.random_base32()
    assert verify_totp(secret, pyotp.TOTP(secret).now())
    assert not verify_totp(secret, "000000" if pyotp.TOTP(secret).now() != "000000" else "111111")
    assert not verify_totp(secret, "")


def test_session_tokens_are_random_and_stored_hashed() -> None:
    (t1, h1), (t2, h2) = new_session_token(), new_session_token()
    assert t1 != t2 and h1 != h2 and len(h1) == 32 and t1.encode() not in h1


EXPECTED = {
    # permission:                (operator, tenant_admin, tenant_viewer)
    Permission.VIEW_LEADS: (True, True, True),
    Permission.UNMASK_PII: (True, True, False),
    Permission.APPROVE_MESSAGES: (True, False, False),
    Permission.MANAGE_TENANT_USERS: (True, True, False),
    Permission.CONFIRM_ATTENDANCE: (True, True, False),
    Permission.RAISE_DISPUTE: (True, True, False),
    Permission.VIEW_ALL_TENANTS: (True, False, False),
    Permission.MANAGE_COMPLIANCE: (True, False, False),
    Permission.SIGN_OFF_GOLIVE: (True, False, False),
}


@pytest.mark.parametrize("perm", list(EXPECTED))
def test_permission_matrix(perm: Permission) -> None:
    got = tuple(has_permission(r, perm) for r in ("operator", "tenant_admin", "tenant_viewer"))
    assert got == EXPECTED[perm]


def test_unknown_role_has_no_permissions() -> None:
    assert not any(has_permission("hacker", p) for p in Permission)
    assert set(ROLE_PERMISSIONS) == {"operator", "tenant_admin", "tenant_viewer"}
