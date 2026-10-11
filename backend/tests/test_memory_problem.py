"""What an error opening memory.db means for the person (`app/stores.py`): a code the
app has words for, or none for a fault of ours, which still stops the start. SQLite's
primary result code decides, not the exception's class."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, get_args

import pytest

from app.core.problems import ProblemCode
from app.left_behind import MemoryLeftBehind
from app.services.memory.migrations import DatabaseTooNewError, MigrationError
from app.services.memory.upgrade_copy import CopyError
from app.stores import memory_problem


def _coded(error: sqlite3.Error, code: int) -> sqlite3.Error:
    error.sqlite_errorcode = code
    return error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (DatabaseTooNewError("newer", found=9), ("memory_too_new", {"copy": ""})),
        (CopyError("failed"), ("memory_copy_failed", {})),
        (
            CopyError("no room", needed_mb=12, free_mb=0),
            ("memory_no_room", {"needed_mb": 12, "free_mb": 0}),
        ),
        (CopyError("the file is damaged", damaged=True), ("memory_damaged", {})),
        (
            _coded(sqlite3.OperationalError("locked"), sqlite3.SQLITE_BUSY),
            ("memory_unopenable", {}),
        ),
        (
            _coded(sqlite3.OperationalError("read-only"), sqlite3.SQLITE_READONLY),
            ("memory_unopenable", {}),
        ),
        (  # an extended code: its low byte is the primary one
            _coded(sqlite3.OperationalError("disk I/O"), sqlite3.SQLITE_IOERR_FSYNC),
            ("memory_unopenable", {}),
        ),
        (
            _coded(sqlite3.DatabaseError("malformed"), sqlite3.SQLITE_CORRUPT),
            ("memory_damaged", {}),
        ),
        (_coded(sqlite3.OperationalError("full"), sqlite3.SQLITE_FULL), ("memory_no_room", {})),
        (PermissionError(13, "denied"), ("memory_unopenable", {})),
        (MemoryLeftBehind(), ("memory_left_behind", {})),
    ],
)
def test_what_each_failure_means_for_the_person(
    error: Exception, expected: tuple[str, dict[str, Any]]
) -> None:
    problem = memory_problem(error)
    assert problem is not None
    assert (problem.code, problem.detail) == expected


@pytest.mark.parametrize(
    "error",
    [
        _coded(sqlite3.OperationalError("syntax error"), sqlite3.SQLITE_ERROR),
        _coded(sqlite3.IntegrityError("constraint"), sqlite3.SQLITE_CONSTRAINT),
        # A clash within one connection: ours, not another program's.
        _coded(sqlite3.OperationalError("table is locked"), sqlite3.SQLITE_LOCKED),
        sqlite3.ProgrammingError("Cannot operate on a closed database."),  # no code at all
        # A step that ended its own transaction: a fault of this version's upgrade.
        MigrationError("a step ended its own transaction"),
        RuntimeError("a bug"),
        ValueError("a bug"),
    ],
)
def test_faults_of_ours_have_no_words(error: Exception) -> None:
    assert memory_problem(error) is None


def test_the_app_has_words_for_every_problem() -> None:
    """Written in the frontend, which can drift: a code with no words there reads as
    "Part of Veronica didn't start" (mutation: a new code with no case)."""
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    copy = (frontend / "src" / "shell" / "problemCopy.ts").read_text(encoding="utf-8")
    for code in get_args(ProblemCode):
        assert f"case '{code}':" in copy, code
