"""Files sealed with AES-256-GCM (ADR 0001): her voice clips, encrypted with a data key
and opened into memory when read, never to disk.

A sealed file is `VRNS` and a version byte, a random 12-byte nonce, then the ciphertext
with GCM's 16-byte tag. The header and the file's own name are authenticated along with
it, so a file changed anywhere, or put in another's place (one voice's clip renamed to
another's), fails to open rather than opening as something else. Each seal draws a new
nonce: random 96-bit nonces are safe under one key for far more files than a voices
folder holds.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"VRNS\x01"
_NONCE = 12
_TAG = 16


class SealBroken(ValueError):
    """A sealed file that doesn't open with the key: changed, put in another's place,
    or sealed with another key."""


def seal(data: bytes, key: bytes, name: str) -> bytes:
    """`data` sealed with `key`, for the file called `name`."""
    nonce = os.urandom(_NONCE)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, _bound(name))


def unseal(blob: bytes, key: bytes, name: str) -> bytes:
    """What `seal` sealed for `name`; `SealBroken` for anything else."""
    if not blob.startswith(MAGIC):
        raise SealBroken("not a sealed file")
    start = len(MAGIC) + _NONCE
    if len(blob) < start + _TAG:  # cut short: AES-GCM would refuse the nonce, not the seal
        raise SealBroken("the file is cut short")
    try:
        return AESGCM(key).decrypt(blob[len(MAGIC) : start], blob[start:], _bound(name))
    except InvalidTag as error:
        raise SealBroken("the file doesn't open with this key") from error


def is_sealed(path: Path) -> bool:
    """Whether the file at `path` starts as a sealed one does."""
    with path.open("rb") as handle:
        return handle.read(len(MAGIC)) == MAGIC


def _bound(name: str) -> bytes:
    return MAGIC + name.encode("utf-8")


def seal_in_place(path: Path, key: bytes, *, marker: str) -> None:
    """Replace the plain file at `path` with its sealed form, never losing it: the sealed
    copy is written beside it (`<name><marker>`), checked to open back to the same bytes
    and flushed to disk before it replaces the file. A file written to meanwhile keeps
    its newer bytes, plain, and is sealed next time."""
    plain = path.read_bytes()
    sealed = seal(plain, key, path.name)
    if unseal(sealed, key, path.name) != plain:  # never: AES-GCM opens what it sealed
        raise OSError("the sealed file doesn't open back to the same bytes")
    temp = path.with_name(path.name + marker)
    try:
        with temp.open("xb") as handle:
            handle.write(sealed)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != plain:
            raise OSError("the file changed while it was sealed")
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
