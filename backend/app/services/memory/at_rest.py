"""Her memory file encrypted in place, once (ADR 0001).

Every build before 0.32.0 wrote it plain. The first start of one after finds it so and
encrypts it with the data key before anything opens it: SQLCipher exports it into a new
encrypted file beside it (`sqlcipher_export`), whose version is set by hand, since the
export doesn't carry `user_version` (measured: 7 came out 0). The new file must read
back whole, by SQLite's own check, the same tables, the same number of rows in each and
the same version, before it takes the plain file's place in one rename. The copies kept
from before upgrades are encrypted the same way, so no plain copy of her memory is left
beside the encrypted one.

A start cut off partway leaves the plain file as it was, and an unfinished file the next
start removes. On a disk whose rename isn't one step (FAT32 and exFAT remove the old
file first), a cut in the middle of the rename leaves only the encrypted file, checked
before it: the next start puts it in place. One that can't encrypt it (no room,
another program holding the file) leaves it plain and says so, and the next start
tries again (`NotEncrypted`); a copy that can't be is said once the others are done.
Each copy keeps its time, from which its 30 days are counted (`upgrade_copy.py`).

What it can't do: wipe the plain file's blocks. The rename frees them, and they stay on
the disk until something is written over them.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import sqlite3
from collections import Counter
from contextlib import closing, suppress
from pathlib import Path

import sqlcipher3

from app.core.data_key import Locked
from app.core.machine import MIB
from app.services.memory import cipher, migrations, upgrade_copy

logger = logging.getLogger(__name__)

_UNFINISHED = ".encrypting"
# An encrypted page reads as noise, about 8 bits a byte; a damaged plain one keeps its
# zeros and its text. Measured on SQLCipher's first pages: 7.9 and more.
_NOISE_BITS = 7.0
_PAGE = 4096  # SQLCipher's page, the least an encrypted file holds


class NotEncrypted(OSError):
    """Her memory file, or the copy `copy` names, couldn't be encrypted at this start,
    and was left as it was. `needed_mb` and `free_mb` are set when there wasn't room for
    the encrypted one."""

    def __init__(
        self,
        message: str,
        *,
        needed_mb: int | None = None,
        free_mb: int | None = None,
        copy: str | None = None,
    ) -> None:
        super().__init__(message)
        self.needed_mb = needed_mb
        self.free_mb = free_mb
        self.copy = copy


def holds_encrypted(path: Path) -> bool:
    """Whether the file at `path` is one SQLCipher encrypted: a page or more, no SQLite
    header, and its first page noise. A damaged plain file, its header gone, is not: it
    is reported as damaged, not as a memory whose key is missing."""
    try:
        with path.open("rb") as handle:
            first = handle.read(_PAGE)
    except FileNotFoundError:
        return False
    return len(first) == _PAGE and not cipher.plain(path) and _bits_per_byte(first) >= _NOISE_BITS


def encrypted_anything(path: Path) -> bool:
    """Whether her memory file at `path`, a copy kept from before an upgrade, or the
    encrypted file a cut rename left in its place is one SQLCipher encrypted: what a new
    data key would lock away."""
    left = [] if path.exists() else [_unfinished(path)]
    return any(holds_encrypted(file) for file in (path, *upgrade_copy.copies(path), *left))


def check_key(path: Path, key: bytes) -> None:
    """`Locked("key_mismatch")` when her encrypted memory file doesn't open with `key`:
    another file's key, or the file damaged where SQLCipher checks the key, which it
    can't tell apart. Any other failure is left to the opening that follows."""
    if not holds_encrypted(path):
        return
    try:
        with closing(cipher.open_sync(path, key)) as db:
            db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except cipher.SQLITE_ERRORS as error:
        if cipher.error_code(error) == sqlite3.SQLITE_NOTADB:
            raise Locked("key_mismatch") from error


def encrypt(path: Path, key: bytes) -> int:
    """Encrypt her memory file at `path`, and each copy kept from before an upgrade, that
    is still plain; how many were. `NotEncrypted` when one couldn't be, with it left as
    it was: at once for her memory file, once the others are done for a copy. Blocking:
    run it off the event loop."""
    _finish_rename(path, key)
    done, failed = 0, None
    for file in (path, *upgrade_copy.copies(path)):
        try:
            done += _encrypt_if_plain(file, key)
        except NotEncrypted as error:
            if file == path:
                raise
            logger.warning("a copy of her memory stays plain for now (%s)", error)
            error.copy = file.name
            failed = failed or error
    if done:
        logger.info("encrypted %d of her memory files", done)
    if failed is not None:
        raise failed
    return done


def _encrypt_if_plain(file: Path, key: bytes) -> int:
    if not file.is_file():
        return 0
    unfinished = _unfinished(file)
    for leftover in (unfinished, Path(f"{unfinished}-journal")):
        leftover.unlink(missing_ok=True)
    if file.stat().st_size == 0 or not cipher.plain(file):
        return 0
    if _version(file) > migrations.LATEST:
        return 0  # a newer build's: refused before anything touches it
    _encrypt(file, unfinished, key)
    return 1


def _finish_rename(path: Path, key: bytes) -> None:
    """Put in place the encrypted file a cut rename left as the only one, if it reads
    back whole: it was checked before the rename began."""
    unfinished = _unfinished(path)
    if path.exists() or not unfinished.is_file() or Path(f"{unfinished}-journal").exists():
        return
    try:
        with closing(cipher.open_sync(unfinished, key)) as db:
            (verdict,) = db.execute("PRAGMA quick_check").fetchone()
    except cipher.SQLITE_ERRORS:
        return
    if verdict == "ok":
        os.replace(unfinished, path)
        logger.warning("finished a rename a start cut short: her memory file is in place")


def _encrypt(file: Path, target: Path, key: bytes) -> None:
    needed = upgrade_copy.room_needed(file)
    free = shutil.disk_usage(file.parent).free
    if free < needed:
        raise NotEncrypted(
            f"There isn't room to encrypt {file.name}",
            needed_mb=upgrade_copy.needed_mib(needed),
            free_mb=free // MIB,
        )
    before = _stamp(file)
    try:
        source = cipher.open_sync(file, None)
        try:
            (version,) = source.execute("PRAGMA user_version").fetchone()
            rows = _rows(source)
            source.execute(f"ATTACH DATABASE ? AS encrypted KEY \"x'{key.hex()}'\"", (str(target),))
            source.execute("SELECT sqlcipher_export('encrypted')")
            source.execute(f"PRAGMA encrypted.user_version = {int(version)}")
            source.execute("DETACH DATABASE encrypted")
        finally:
            source.close()
        _check(target, key, int(version), rows)
    except cipher.SQLITE_OR_OS_ERRORS as error:
        with suppress(OSError):  # held by a scan, say: the next start removes it
            target.unlink(missing_ok=True)
        raise NotEncrypted(f"{file.name} couldn't be encrypted") from error
    # Its journal, left beside it, would be played into the encrypted file at its next
    # open, and a write since the export would be lost with the plain file: a file in use
    # elsewhere is left plain, and tried again next time.
    journal = any(Path(f"{file}{end}").exists() for end in ("-journal", "-wal"))
    try:
        if journal or _stamp(file) != before:
            raise OSError("written to while it was encrypted")
        found = file.stat()  # a copy's 30 days count from when it was made
        os.utime(target, ns=(found.st_atime_ns, found.st_mtime_ns))
        os.replace(target, file)
    except OSError as error:
        with suppress(OSError):
            target.unlink(missing_ok=True)
        raise NotEncrypted(f"{file.name} is in use") from error


def _check(target: Path, key: bytes, version: int, rows: dict[str, int]) -> None:
    """Raise unless `target` reads back as the file it was made from."""
    db = cipher.open_sync(target, key)
    try:
        (verdict,) = db.execute("PRAGMA quick_check").fetchone()
        (stamped,) = db.execute("PRAGMA user_version").fetchone()
        found = _rows(db)
    finally:
        db.close()
    if verdict != "ok" or stamped != version or found != rows:
        raise sqlcipher3.DatabaseError(f"the encrypted copy reads {verdict!r} at version {stamped}")


def _rows(db: sqlcipher3.Connection) -> dict[str, int]:
    """Every ordinary table and its number of rows. A virtual table's rows are in its
    shadow tables, counted with the rest, and reading it would need its module."""
    tables = [
        name
        for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
        if not str(sql or "").upper().startswith("CREATE VIRTUAL TABLE")
    ]
    return {
        name: db.execute(f"SELECT COUNT(*) FROM {_quoted(name)}").fetchone()[0] for name in tables
    }


def _unfinished(file: Path) -> Path:
    return file.with_name(f"{file.name}{_UNFINISHED}")


def _version(file: Path) -> int:
    """The version a plain file was written at, read from its header without opening it:
    SQLite keeps `user_version` at byte 60, big-endian."""
    with file.open("rb") as handle:
        handle.seek(60)
        return int.from_bytes(handle.read(4), "big")


def _bits_per_byte(data: bytes) -> float:
    counts = Counter(data)
    return -sum(n / len(data) * math.log2(n / len(data)) for n in counts.values())


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _stamp(file: Path) -> tuple[int, int]:
    found = file.stat()
    return found.st_size, found.st_mtime_ns
