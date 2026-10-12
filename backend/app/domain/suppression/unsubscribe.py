"""Signed, tamper-proof one-click unsubscribe tokens.

Token = base64url(token_id[16] || HMAC-SHA256(key, "unsub:v1:" || token_id)[:16]). No PII in the URL.
Verification is constant-time. Unsubscribing is idempotent and always safe (worst case: over-suppression).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

PREFIX = b"unsub:v1:"


class InvalidToken(Exception):
    pass


def _mac(key: bytes, token_id: uuid.UUID) -> bytes:
    return hmac.new(key, PREFIX + token_id.bytes, hashlib.sha256).digest()[:16]


def sign(key: bytes, token_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(token_id.bytes + _mac(key, token_id)).decode().rstrip("=")


def verify(key: bytes, token: str) -> uuid.UUID:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError) as exc:
        raise InvalidToken("malformed") from exc
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != token:
        raise InvalidToken("malformed or non-canonical")
    token_id = uuid.UUID(bytes=raw[:16])
    if not hmac.compare_digest(raw[16:], _mac(key, token_id)):
        raise InvalidToken("bad signature")
    return token_id
