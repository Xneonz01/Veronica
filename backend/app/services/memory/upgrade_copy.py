"""The copy of memory.db kept from before an upgrade: the way back to the build before.

An earlier Veronica refuses a file upgraded past what it knows (`migrations`). The copy
is the file as it was when the upgrade began, which that build opens: `memory.db.v5.bak`
before an upgrade from 5. Since 0.32.0 the copies are encrypted as the file is
(`at_rest.py`), so a build before 0.32.0 opens neither: going back that far is no
longer possible. It is made with SQLite's own backup (every page as one
consistent snapshot, the write-ahead log included) into a `.partial` file, checked, and
only then given its name, so a copy cut short by a full disk or a crash is never taken
for one. It is made again at every upgrade that begins, so going back and coming
forward again keeps the newest copy, not a stale one.

A copy is deleted 30 days or more after it was made, by a start that opened and
upgraded the file (`app/stores.py`): until then it still holds what has been deleted
since, from her memory, conversations and documents, and a start that finds the file
damaged or too new keeps every copy, since one of them is the way back.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing
from fractions import Fraction
from pathlib import Path

import aiosqlite

from app.core.machine import MIB
from app.services.memory import cipher

logger = logging.getLogger(__name__)

COPY_DAYS = 30
_PARTIAL_HOURS = 24  # a partial copy older than this was left by a start that stopped
# The copy takes the file's size again, and a little for its journal. A fraction, so a
# size in bytes comes out exact (1,500 * 1.1 is 1,650.0000000000002 as a float).
_ROOM = Fraction(11, 10)


class CopyError(OSError):
    """The copy from before an upgrade couldn't be made. The upgrade waits for it: the
    file is left as it was. `needed_mb` and `free_mb` are set when there wasn't room (in
    MiB, shown as MB as Windows shows sizes); `damaged` when the file itself fails
    SQLite's check, so no copy of it could."""

    def __init__(
        self,
        message: str,
        *,
        needed_mb: int | None = None,
        free_mb: int | None = None,
        damaged: bool = False,
    ) -> None:
        super().__init__(message)
        self.needed_mb = needed_mb
        self.free_mb = free_mb
        self.damaged = damaged


def name_for(path: Path, version: int) -> Path:
    return path.with_name(f"{path.name}.v{version}.bak")


def newest(path: Path, at_most: int) -> Path | None:
    """The copy a build that knows versions up to `at_most` can open: the newest of them."""
    usable = {version: copy for version, copy in _copies(path).items() if version <= at_most}
    return usable[max(usable)] if usable else None


def room_needed(path: Path) -> int:
    """The free space, in bytes, beside the file at `path` that its copy before an
    upgrade needs; 0 while there is no file. The check in Settings asks it too."""
    return math.ceil(sum(_size(Path(f"{path}{end}")) for end in ("", "-wal")) * _ROOM)


def needed_mib(size: int) -> int:
    """A size needed, in whole MiB rounded up: with what is free rounded down
    (`machine.free_mib`), a shortfall never reads as enough, and the notice at start and
    the check in Settings give the same figures."""
    return -(-size // MIB)


async def keep(db: aiosqlite.Connection, path: Path, version: int, key: bytes | None) -> Path:
    """Copy the file at `path` (open as `db`, at `version`) beside itself; the copy.
    Encrypted with the file's own `key`, so no plain copy of her memory is left beside
    it. CopyError when there isn't room for it, or it doesn't check out."""
    final = name_for(path, version)
    partial = final.with_name(f"{final.name}.partial")
    _remove(partial)
    _remove(partial.with_name(f"{partial.name}-journal"))
    needed = room_needed(path)
    free = shutil.disk_usage(path.parent).free
    if free < needed:
        needed_mb, free_mb = needed_mib(needed), free // MIB
        raise CopyError(
            f"There isn't room to keep a copy of {path.name} before upgrading it: it needs "
            f"{needed_mb} MB and {free_mb} MB are free.",
            needed_mb=needed_mb,
            free_mb=free_mb,
        )
    try:
        async with cipher.connect(partial, key) as target:
            await db.backup(target)
        await asyncio.to_thread(_check, partial, version, key)
        os.replace(partial, final)
    except cipher.SQLITE_OR_OS_ERRORS as error:
        _remove(partial)
        # The backup copies pages as they are: a damaged file makes a damaged copy, and
        # trying again won't change that.
        if await _damaged(db):
            raise CopyError(f"{path.name} is damaged.", damaged=True) from error
        raise CopyError(f"The copy of {path.name} from before its upgrade failed.") from error
    logger.info("kept a copy of %s from before its upgrade: %s", path.name, final.name)
    return final


def remove_old(path: Path, *, now: float | None = None) -> list[Path]:
    """Delete the copies of `path` made COPY_DAYS ago or more, and partial ones a start
    that stopped left behind; the ones deleted."""
    clock = time.time() if now is None else now
    copies = list(_copies(path).values())
    partial = re.compile(re.escape(path.name) + r"\.v\d+\.bak\.partial(-journal)?")
    stale = [
        entry
        for entry in path.parent.iterdir()
        if partial.fullmatch(entry.name) and _age(entry, clock) >= _PARTIAL_HOURS * 3600
    ]
    removed = [copy for copy in copies if _age(copy, clock) >= COPY_DAYS * 86_400]
    for entry in (*removed, *stale):
        _remove(entry)
    return [entry for entry in (*removed, *stale) if not entry.exists()]


def copies(path: Path) -> list[Path]:
    """Every copy of `path` kept from before an upgrade, oldest version first."""
    found = _copies(path)
    return [found[version] for version in sorted(found)]


def _copies(path: Path) -> dict[int, Path]:
    """The copies of `path` by the version each was made at, found by their exact name:
    `memory.db.very-important.bak` is someone's own file, not one of these."""
    pattern = re.compile(re.escape(path.name) + r"\.v(\d+)\.bak")
    found: dict[int, Path] = {}
    if not path.parent.is_dir():
        return found
    for entry in path.parent.iterdir():
        match = pattern.fullmatch(entry.name)
        if match and entry.is_file() and not entry.is_symlink():
            found[int(match.group(1))] = entry
    return found


def _check(copy: Path, version: int, key: bytes | None) -> None:
    """Raise unless `copy` reads as a whole database at `version`."""
    with closing(cipher.open_sync(copy, key)) as db:
        (verdict,) = db.execute("PRAGMA quick_check").fetchone()
        (stamped,) = db.execute("PRAGMA user_version").fetchone()
    if verdict != "ok" or stamped != version:
        raise sqlite3.DatabaseError(f"the copy reads {verdict!r} at version {stamped}")


async def _damaged(db: aiosqlite.Connection) -> bool:
    """Whether the open file fails SQLite's check; an error of another kind (a lock, a
    read that failed) says nothing either way."""
    try:
        async with db.execute("PRAGMA quick_check") as cursor:
            row = await cursor.fetchone()
    except cipher.SQLITE_ERRORS as error:
        return cipher.error_code(error) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
    return row is None or row[0] != "ok"


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _age(path: Path, now: float) -> float:
    try:
        return now - path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # held open elsewhere: the next start tries again
        logger.warning("could not remove %s", path.name)
