"""PII cryptography (ADR-0005).

- Envelope encryption: per-tenant AES-256-GCM data keys, wrapped by the master key, stored in
  ``tenant_keys``. Ciphertext format: version(1) | key_id(16) | nonce(12) | ciphertext+tag.
- AAD binds each ciphertext to its tenant and purpose (e.g. ``prospects.email``), so ciphertexts
  cannot be swapped between columns or tenants.
- Blind indexes (tenant-salted HMAC) for exact-match lookup without decrypting.
- Suppression hashes use a *separate global* key so suppression survives erasure / key shredding.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.engine import Connection

FORMAT_VERSION = b"\x01"
GLOBAL_SCOPE = uuid.UUID(int=0)  # data key for non-tenant PII (e.g. operator users)


class CryptoError(Exception):
    pass


def normalise_email(email: str) -> str:
    """Trim, NFKC, lowercase. No provider-specific dot/plus stripping (corporate addresses)."""
    return unicodedata.normalize("NFKC", email).strip().lower()


def normalise_domain(domain: str) -> str:
    d = unicodedata.normalize("NFKC", domain).strip().lower().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    try:
        return d.encode("idna").decode("ascii")
    except UnicodeError:
        return d


def email_domain(email: str) -> str:
    return normalise_domain(normalise_email(email).rsplit("@", 1)[-1])


def blind_index(key: bytes, value: str, scope: uuid.UUID | None) -> bytes:
    salt = (scope or GLOBAL_SCOPE).bytes
    return hmac.new(key, salt + normalise_email(value).encode(), hashlib.sha256).digest()


IdentifierType = Literal["email", "domain"]


def suppression_hash(key: bytes, value: str, identifier_type: IdentifierType) -> bytes:
    norm = normalise_email(value) if identifier_type == "email" else normalise_domain(value)
    return hmac.new(key, f"{identifier_type}:{norm}".encode(), hashlib.sha256).digest()


def _wrap(master: bytes, data_key: bytes, key_id: uuid.UUID) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(master).encrypt(nonce, data_key, b"tenant-key:" + key_id.bytes)


def _unwrap(master: bytes, wrapped: bytes, key_id: uuid.UUID) -> bytes:
    try:
        return AESGCM(master).decrypt(wrapped[:12], wrapped[12:], b"tenant-key:" + key_id.bytes)
    except Exception as exc:  # cryptography raises InvalidTag
        raise CryptoError("unable to unwrap data key") from exc


@dataclass
class PiiCipher:
    """Encrypts/decrypts PII using per-tenant data keys loaded from ``tenant_keys``."""

    master_key: bytes
    _cache: dict[uuid.UUID, tuple[uuid.UUID, bytes]] = field(default_factory=dict)
    _by_id: dict[uuid.UUID, bytes] = field(default_factory=dict)

    def _active_key(self, conn: Connection, scope: uuid.UUID) -> tuple[uuid.UUID, bytes]:
        if scope in self._cache:
            return self._cache[scope]
        row = conn.execute(
            text(
                "SELECT id, wrapped_key FROM tenant_keys WHERE scope_id = :s AND destroyed_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"s": scope},
        ).first()
        if row is None:
            key_id, data_key = uuid.uuid4(), AESGCM.generate_key(bit_length=256)
            conn.execute(
                text("INSERT INTO tenant_keys (id, scope_id, wrapped_key) VALUES (:id, :s, :w)"),
                {"id": key_id, "s": scope, "w": _wrap(self.master_key, data_key, key_id)},
            )
        else:
            key_id, data_key = row.id, _unwrap(self.master_key, bytes(row.wrapped_key), row.id)
        self._cache[scope] = (key_id, data_key)
        self._by_id[key_id] = data_key
        return key_id, data_key

    def _key_by_id(self, conn: Connection, key_id: uuid.UUID) -> bytes:
        if key_id in self._by_id:
            return self._by_id[key_id]
        row = conn.execute(
            text("SELECT wrapped_key FROM tenant_keys WHERE id = :id AND destroyed_at IS NULL"), {"id": key_id}
        ).first()
        if row is None:
            raise CryptoError("data key not found or destroyed")
        key = _unwrap(self.master_key, bytes(row.wrapped_key), key_id)
        self._by_id[key_id] = key
        return key

    def encrypt(self, conn: Connection, scope: uuid.UUID | None, purpose: str, plaintext: str) -> bytes:
        s = scope or GLOBAL_SCOPE
        key_id, key = self._active_key(conn, s)
        nonce = os.urandom(12)
        aad = s.bytes + purpose.encode()
        return FORMAT_VERSION + key_id.bytes + nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), aad)

    def decrypt(self, conn: Connection, scope: uuid.UUID | None, purpose: str, blob: bytes) -> str:
        s = scope or GLOBAL_SCOPE
        blob = bytes(blob)
        if blob[:1] != FORMAT_VERSION or len(blob) < 1 + 16 + 12 + 16:
            raise CryptoError("unsupported ciphertext format")
        key_id = uuid.UUID(bytes=blob[1:17])
        key = self._key_by_id(conn, key_id)
        try:
            return AESGCM(key).decrypt(blob[17:29], blob[29:], s.bytes + purpose.encode()).decode()
        except Exception as exc:
            raise CryptoError("decryption failed (wrong key, tenant or purpose)") from exc

    def rotate(self, conn: Connection, scope: uuid.UUID | None) -> uuid.UUID:
        """Create a new active data key; old keys remain for decryption until re-encryption."""
        s = scope or GLOBAL_SCOPE
        key_id, data_key = uuid.uuid4(), AESGCM.generate_key(bit_length=256)
        conn.execute(
            text(
                "INSERT INTO tenant_keys (id, scope_id, wrapped_key, created_at) "
                "VALUES (:id, :s, :w, clock_timestamp())"
            ),
            {"id": key_id, "s": s, "w": _wrap(self.master_key, data_key, key_id)},
        )
        self._cache[s] = (key_id, data_key)
        self._by_id[key_id] = data_key
        return key_id
