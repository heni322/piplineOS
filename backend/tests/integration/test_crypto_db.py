from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.crypto import CryptoError, PiiCipher
from app.db.session import DbContext, transaction
from tests.factories import make_tenant

pytestmark = pytest.mark.db


def test_roundtrip_and_binding(clean_db, cipher: PiiCipher, settings) -> None:  # type: ignore[no-untyped-def]
    a, b = make_tenant(clean_db), make_tenant(clean_db)
    with transaction(DbContext.system(a), clean_db) as conn:
        blob = cipher.encrypt(conn, a, "prospects.email", "alex@contoso.example")
        assert b"alex" not in blob
        assert cipher.decrypt(conn, a, "prospects.email", blob) == "alex@contoso.example"
        with pytest.raises(CryptoError):
            cipher.decrypt(conn, a, "prospects.name", blob)  # wrong purpose
        with pytest.raises(CryptoError):
            cipher.decrypt(conn, b, "prospects.email", blob)  # wrong tenant
        assert cipher.encrypt(conn, a, "p", "x") != cipher.encrypt(conn, a, "p", "x")  # random nonce


def test_rotation_keeps_old_ciphertexts_readable(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    c1 = PiiCipher(settings.key("pii_master_key"))
    with transaction(DbContext.system(t), clean_db) as conn:
        old = c1.encrypt(conn, t, "p", "before rotation")
        new_key = c1.rotate(conn, t)
        new = c1.encrypt(conn, t, "p", "after rotation")
        assert uuid.UUID(bytes=bytes(new)[1:17]) == new_key
    c2 = PiiCipher(settings.key("pii_master_key"))  # fresh process, empty cache
    with transaction(DbContext.system(t), clean_db) as conn:
        assert c2.decrypt(conn, t, "p", old) == "before rotation"
        assert c2.decrypt(conn, t, "p", new) == "after rotation"


def test_destroyed_key_makes_data_unreadable(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    with transaction(DbContext.system(t), clean_db) as conn:
        blob = PiiCipher(settings.key("pii_master_key")).encrypt(conn, t, "p", "secret")
        conn.execute(text("UPDATE tenant_keys SET destroyed_at = now() WHERE scope_id = :t"), {"t": t})
    with transaction(DbContext.system(t), clean_db) as conn, pytest.raises(CryptoError):
        PiiCipher(settings.key("pii_master_key")).decrypt(conn, t, "p", blob)


def test_wrong_master_key_cannot_unwrap(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    with transaction(DbContext.system(t), clean_db) as conn:
        blob = PiiCipher(settings.key("pii_master_key")).encrypt(conn, t, "p", "secret")
        with pytest.raises(CryptoError):
            PiiCipher(b"\x00" * 32).decrypt(conn, t, "p", blob)
