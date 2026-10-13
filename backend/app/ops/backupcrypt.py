"""Encrypts database backups at rest: AES-256-GCM in 1 MiB chunks, key from a passphrase via scrypt.

Format: b"POSBK1" | salt(16) | nonce_prefix(8) | chunks..., each chunk = len(4, big-endian) | ciphertext+tag.
Every chunk's nonce = prefix || counter, and its AAD marks whether it is the final chunk, so reordering, dropping
or truncating chunks is detected on decryption. Streams stdin -> stdout:

    pg_dump -Fc ... | python3 backupcrypt.py encrypt --passphrase-file F > backup.enc
    python3 backupcrypt.py decrypt --passphrase-file F < backup.enc | pg_restore ...

Standalone on purpose (only the `cryptography` package), so the backup image needs nothing else from the app.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"POSBK1"
CHUNK = 1024 * 1024


class BackupCorrupt(Exception):
    pass


def _key(passphrase: bytes, salt: bytes) -> bytes:
    if len(passphrase) < 20:
        raise ValueError("backup passphrase must be at least 20 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase)


def _nonce(prefix: bytes, i: int) -> bytes:
    return prefix + struct.pack(">I", i)


def encrypt(src: BinaryIO, dst: BinaryIO, passphrase: bytes) -> None:
    salt, prefix = os.urandom(16), os.urandom(8)
    aes = AESGCM(_key(passphrase, salt))
    dst.write(MAGIC + salt + prefix)
    i, buf = 0, src.read(CHUNK)
    while True:
        nxt = src.read(CHUNK)
        final = not nxt
        ct = aes.encrypt(_nonce(prefix, i), buf, b"final" if final else b"more")
        dst.write(struct.pack(">I", len(ct)) + ct)
        if final:
            return
        i, buf = i + 1, nxt


def decrypt(src: BinaryIO, dst: BinaryIO, passphrase: bytes) -> None:
    head = src.read(len(MAGIC) + 24)
    if len(head) != len(MAGIC) + 24 or not head.startswith(MAGIC):
        raise BackupCorrupt("not a PipelineOS backup")
    salt, prefix = head[6:22], head[22:30]
    aes = AESGCM(_key(passphrase, salt))
    i = 0
    while True:
        hdr = src.read(4)
        if len(hdr) != 4:
            raise BackupCorrupt("truncated backup (final chunk missing)")
        (n,) = struct.unpack(">I", hdr)
        ct = src.read(n)
        if len(ct) != n:
            raise BackupCorrupt("truncated chunk")
        for aad in (b"more", b"final"):
            try:
                pt = aes.decrypt(_nonce(prefix, i), ct, aad)
            except InvalidTag:  # not this AAD (or tampered): try the other, then give up
                continue
            dst.write(pt)
            if aad == b"final":
                if src.read(1):
                    raise BackupCorrupt("data after the final chunk")
                return
            break
        else:
            raise BackupCorrupt("wrong passphrase or tampered backup")
        i += 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("mode", choices=["encrypt", "decrypt"])
    p.add_argument("--passphrase-file", required=True)
    a = p.parse_args()
    with open(a.passphrase_file, "rb") as f:
        passphrase = f.read().strip()
    try:
        (encrypt if a.mode == "encrypt" else decrypt)(sys.stdin.buffer, sys.stdout.buffer, passphrase)
    except BackupCorrupt as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
