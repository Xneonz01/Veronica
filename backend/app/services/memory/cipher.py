"""Her memory file through SQLCipher (ADR 0001): every connection to it is made here.

SQLCipher is SQLite with every page encrypted (AES-256), keyed with the data key
(`data_key.py`). The binding, `sqlcipher3` from sqlcipher3-wheels, is the standard
library's module built against it. Without a key a connection reads a plain file, as
the tests' stores and a file not yet encrypted do. Two differences from `sqlite3`
matter: its errors are classes of its own, not the standard library's (`SQLITE_ERRORS`
holds both), and its rows are its own `Row`. SQLCipher reports a wrong key on stderr as
well as by raising; its log is off, since the raise says it.

A file SQLCipher encrypted has no SQLite header, its first bytes are random, so `plain`
tells the two apart without opening either.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import aiosqlite
import sqlcipher3

#: What a read or a write of her file can raise: SQLCipher's classes, which don't derive
#: from the standard library's, and the standard library's own.
SQLITE_ERRORS: tuple[type[Exception], ...] = (sqlcipher3.Error, sqlite3.Error)
OPERATIONAL_ERRORS: tuple[type[Exception], ...] = (
    sqlcipher3.OperationalError,
    sqlite3.OperationalError,
)
#: The same, or an error of the file system.
SQLITE_OR_OS_ERRORS: tuple[type[Exception], ...] = (*SQLITE_ERRORS, OSError)
#: The row factory of every connection made here: aiosqlite's `Row` is the standard
#: library's, which refuses a SQLCipher cursor.
ROW = sqlcipher3.Row
_HEADER = b"SQLite format 3\x00"


def connect(path: Path | str, key: bytes | None) -> aiosqlite.Connection:
    """An aiosqlite connection to `path` through SQLCipher, keyed when `key` is given.
    Awaited (or used with `async with`) like `aiosqlite.connect`."""

    def connector() -> sqlite3.Connection:
        # The same module built against SQLCipher: aiosqlite needs nothing else from it.
        return cast(sqlite3.Connection, open_sync(path, key))

    return aiosqlite.Connection(connector, 64)


def open_sync(path: Path | str, key: bytes | None) -> sqlcipher3.Connection:
    """A blocking connection, for work already off the event loop."""
    connection = sqlcipher3.connect(str(path))
    try:
        connection.execute("PRAGMA cipher_log_level = NONE")
        if key is not None:
            connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    except BaseException:
        connection.close()
        raise
    return connection


def plain(path: Path) -> bool:
    """Whether the file at `path` is SQLite with no encryption: it starts with SQLite's
    header. An empty file is neither, and reads as encrypted."""
    with path.open("rb") as handle:
        return handle.read(len(_HEADER)) == _HEADER


def error_code(error: BaseException) -> int | None:
    """SQLite's primary result code of `error`, from either module's classes."""
    code = getattr(error, "sqlite_errorcode", None)
    return code & 0xFF if isinstance(code, int) else None
