"""A data key: 32 random bytes that encrypt her memory file or seal her voice clips
(ADR 0001), kept wrapped by Windows for the account she runs as (`dpapi.py`) in a key
file beside what it encrypts (`memory.key` beside `memory.db`), so the two move
together: a folder moved by hand, or a data folder changed while the file keeps a place
of its own, can't part them.

The key file holds the key only as Windows wrapped it: a copy of the folder, a backup or
a stolen disk carries it along, and it opens only in that account on that computer, to
whoever can sign in to it. The entropy sets her keys apart from other programs' DPAPI
data. It is in the published source, so it is no secret, and it can never change: every
key wrapped with the old one would be lost.

A key is made only when making one can lose nothing: when nothing encrypted exists. With
something encrypted, a key file that won't unwrap (another Windows account, a reset
profile), one that isn't a key file, and none at all are each a lock the start reports
(`Locked`), and nothing is written over. With nothing encrypted, such a file is set aside
and a new key made. The caller holds the key's lock (`file_lock.py`) across opening it
and encrypting with it, so two starts at once can't each make a key and one encrypt
with the key the other then set aside.

Where there is no DPAPI (not Windows), there is no key: what is plain stays plain, and
the start says so.

A recovery key, when one is made (`core/recovery_key.py`), seals the data key a second
time, kept in the same key file beside the DPAPI wrapping (`recovery`, with the
recovery key's id and the time it was made), so the file carries both and moves with
what it opens. Every key file is sealed with it or none is (`seal_recovery`). Opened
with it where Windows can't unwrap the file, the key is wrapped for this account and
saved (`recover`). A key file is rewritten whole, beside it and then over it.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
import time
from collections.abc import Mapping
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

from app.core import dpapi
from app.core.sealed import MAGIC, SealBroken, seal, unseal

logger = logging.getLogger(__name__)

KEY_BYTES = 32
_ENTROPY = b"veronica-data-key-1"  # never change: see the module docstring
_FORMAT = 1
# How every DPAPI blob begins: its version, 1, and the provider's GUID
# {df9d8cd0-1501-11d1-8c7a-00c04fc297eb} (measured on Windows 11).
_DPAPI_BLOB = bytes.fromhex("01000000d08c9ddf0115d1118c7a00c04fc297eb")


class Locked(Exception):
    """Her encrypted data can't be opened here. `reason` is one the app has words for:
    `another_account` (Windows won't unwrap the key: another account, a reset profile, or
    no Windows at all), `key_damaged` (the key file isn't one), `key_missing` (encrypted
    data, no key file) or `key_mismatch` (the key unwraps, but the data doesn't open with
    it: another file's key, or the file damaged where the key is checked).
    `recoverable` when the key file holds a recovery key's sealing (`recover`)."""

    def __init__(self, reason: str, *, recoverable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.recoverable = recoverable


class Recovery(StrEnum):
    """What a recovery key made of a key file (`recover`)."""

    NONE = "none"  # it holds no recovery key's sealing
    WRONG = "wrong"  # the recovery key isn't the one it was sealed with
    OPEN = "open"  # the right one, and Windows unwraps the file here as well
    RESTORED = "restored"  # the right one: the key is wrapped for this account and saved


class RecoveryMark(NamedTuple):
    """Which recovery key sealed a key file, and when that key was made."""

    key_id: str
    made: str


def key_path(data: Path) -> Path:
    """Where the key to the file at `data` is kept: beside it (`memory.db` -> `memory.key`)."""
    return data.with_suffix(".key")


def open_key(path: Path, *, encrypted: bool) -> bytes | None:
    """The data key from the key file at `path`, made there first when nothing is
    encrypted yet (`encrypted`) and there is no usable one. `Locked` otherwise. None
    where the system has no DPAPI and nothing is encrypted: there is nothing to keep a
    key with."""
    if not dpapi.available():
        if encrypted:
            raise Locked("another_account")
        return None
    try:
        return _unwrap(path.read_bytes())
    except FileNotFoundError:
        if encrypted:
            raise Locked("key_missing") from None
    except Locked:
        if encrypted:
            raise
        _set_aside(path)  # it locks nothing: a new key loses nothing
    return _create(path)


def seal_recovery(keys: Mapping[Path, bytes], sealing: bytes, key_id: str, made: str) -> None:
    """Seal each data key in `keys`, by its key file, a second time with a recovery
    key's `sealing` key, marked with that key's `key_id` and when it was `made`; the
    DPAPI wrappings stay. Every file or none: a key file whose wrapping no longer holds
    the key given is `Locked("key_mismatch")` before anything is written, since
    `recover` later trusts the sealing, and the files change together (`_save_all`).
    The caller holds every key's lock."""
    files: dict[Path, dict[str, Any]] = {}
    for path, key in keys.items():
        raw = path.read_bytes()
        if _unwrap(raw) != key:
            raise Locked("key_mismatch")
        stored = _stored(raw)
        stored |= {
            "recovery": base64.b64encode(seal(key, sealing, path.name)).decode("ascii"),
            "recovery_id": key_id,
            "recovery_made": made,
        }
        files[path] = stored
    _save_all(files)


def recover(path: Path, sealing: bytes) -> Recovery:
    """Open the key file at `path` with a recovery key's `sealing` key. Where Windows
    can't unwrap it here, the key the recovery key opens is wrapped for this account and
    saved. One Windows does unwrap to another key is left as it is: the sealing isn't its
    own. The caller holds the key's lock."""
    raw = path.read_bytes()
    try:
        stored = _stored(raw)
    except Locked:
        return Recovery.NONE
    blob = _recovery(stored)
    if blob is None:
        return Recovery.NONE
    try:
        key = unseal(blob, sealing, path.name)
    except SealBroken:
        return Recovery.WRONG
    try:
        current: bytes | None = _unwrap(raw)
    except Locked:
        current = None
    if current is not None:
        return Recovery.OPEN if current == key else Recovery.WRONG
    stored["dpapi"] = base64.b64encode(dpapi.protect(key, _ENTROPY)).decode("ascii")
    _save_all({path: stored})
    logger.warning("a recovery key opened %s, wrapped again for this account", path.name)
    return Recovery.RESTORED


def recovery_mark(path: Path) -> RecoveryMark | None:
    """Which recovery key sealed the key file at `path`, and when it was made; None when
    none did, or the file can't be read."""
    try:
        stored = _stored(path.read_bytes())
    except (OSError, Locked):
        return None
    key_id, made = stored.get("recovery_id"), stored.get("recovery_made")
    if _recovery(stored) is None or not isinstance(key_id, str) or not isinstance(made, str):
        return None
    return RecoveryMark(key_id, made)


def _unwrap(raw: bytes) -> bytes:
    stored = _stored(raw)
    recoverable = _recovery(stored) is not None
    try:
        blob = base64.b64decode(stored["dpapi"], validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error) as error:
        raise Locked("key_damaged", recoverable=recoverable) from error
    if not blob.startswith(_DPAPI_BLOB):  # empty, cut short or not DPAPI's at all
        raise Locked("key_damaged", recoverable=recoverable)
    try:
        key = dpapi.unprotect(blob, _ENTROPY)
    except dpapi.DpapiError as error:
        raise Locked("another_account", recoverable=recoverable) from error
    if len(key) != KEY_BYTES:
        raise Locked("key_damaged", recoverable=recoverable)
    return key


def _stored(raw: bytes) -> dict[str, Any]:
    """A key file's fields; `Locked("key_damaged")` for one this build can't read."""
    try:
        stored = json.loads(raw)
    except ValueError as error:
        raise Locked("key_damaged") from error
    if not isinstance(stored, dict) or stored.get("format") != _FORMAT:
        raise Locked("key_damaged")
    return stored


def _recovery(stored: dict[str, Any]) -> bytes | None:
    """The recovery key's sealing of the data key, if the file holds a readable one."""
    try:
        blob = base64.b64decode(stored["recovery"], validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error):
        return None
    return blob if blob.startswith(MAGIC) else None


def _save_all(files: Mapping[Path, dict[str, Any]]) -> None:
    """Write key files whole: each staged beside it, then all put in place. One that
    can't be put in place (a reader holding it, on Windows) puts back those that were,
    so they change together, and a save cut short leaves the last."""
    before = {path: path.read_bytes() for path in files}
    staged: dict[Path, Path] = {}
    placed: list[Path] = []
    try:
        for path, stored in files.items():
            staged[path] = _stage(path, json.dumps(stored).encode("utf-8"))
        for path, temp in staged.items():
            os.replace(temp, path)
            placed.append(path)
    except OSError:
        for path in placed:
            with suppress(OSError):  # left as it is: the status says the files disagree
                os.replace(_stage(path, before[path]), path)
        for temp in staged.values():
            with suppress(OSError):
                temp.unlink(missing_ok=True)
        raise


def _stage(path: Path, body: bytes) -> Path:
    temp = path.with_name(f"{path.name}.saving")
    with temp.open("wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    return temp


def _create(path: Path) -> bytes:
    key = secrets.token_bytes(KEY_BYTES)
    wrapped = base64.b64encode(dpapi.protect(key, _ENTROPY)).decode("ascii")
    body = json.dumps({"format": _FORMAT, "dpapi": wrapped}).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        # Made meanwhile by something not holding the lock: its key is the one to use.
        return _unwrap(path.read_bytes())
    logger.info("made a data key, %s", path.name)
    return key


def _set_aside(path: Path) -> None:
    aside = path.with_name(f"{path.name}.unusable-{time.strftime('%Y%m%d-%H%M%S')}")
    os.replace(path, aside)
    logger.warning("set aside a key file that couldn't be used, with nothing encrypted by it")
