"""Backup encryption: round trip, and every way a backup can be damaged or forged is detected."""

from __future__ import annotations

import io
import os

import pytest

from app.ops import backupcrypt
from app.ops.backupcrypt import BackupCorrupt, decrypt, encrypt

PW = b"correct horse battery staple, long enough"


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backupcrypt, "CHUNK", 1000)


def _enc(data: bytes) -> bytes:
    out = io.BytesIO()
    encrypt(io.BytesIO(data), out, PW)
    return out.getvalue()


def _dec(blob: bytes, pw: bytes = PW) -> bytes:
    out = io.BytesIO()
    decrypt(io.BytesIO(blob), out, pw)
    return out.getvalue()


@pytest.mark.parametrize("size", [0, 1, 999, 1000, 1001, 5555])
def test_round_trip(size: int) -> None:
    data = os.urandom(size)
    blob = _enc(data)
    assert _dec(blob) == data and data[:16] not in blob[30:] if size >= 16 else _dec(blob) == data


def test_wrong_passphrase_and_tampering_are_detected() -> None:
    blob = bytearray(_enc(os.urandom(3000)))
    with pytest.raises(BackupCorrupt):
        _dec(bytes(blob), b"a different passphrase of length")
    blob[100] ^= 1
    with pytest.raises(BackupCorrupt):
        _dec(bytes(blob))


def test_truncation_and_reordering_are_detected() -> None:
    blob = _enc(os.urandom(3500))  # 4 chunks
    head, body = blob[:30], blob[30:]
    chunks, i = [], 0
    while i < len(body):
        n = int.from_bytes(body[i : i + 4], "big")
        chunks.append(body[i : i + 4 + n])
        i += 4 + n
    assert len(chunks) == 4
    with pytest.raises(BackupCorrupt):  # last chunk dropped
        _dec(head + b"".join(chunks[:-1]))
    with pytest.raises(BackupCorrupt):  # two chunks swapped
        _dec(head + chunks[1] + chunks[0] + chunks[2] + chunks[3])
    with pytest.raises(BackupCorrupt):  # junk appended
        _dec(blob + b"x")
    with pytest.raises(BackupCorrupt):
        _dec(b"not a backup at all, definitely")


def test_short_passphrase_is_refused() -> None:
    with pytest.raises(ValueError):
        encrypt(io.BytesIO(b"x"), io.BytesIO(), b"short")
