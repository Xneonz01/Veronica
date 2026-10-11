"""The copy of memory.db kept from before an upgrade: made whole or not at all, made
again for every upgrade, named exactly, deleted after 30 days, and named when a newer
file is refused."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from app.services.memory import cipher, schema, upgrade_copy
from app.services.memory.migrations import LATEST, DatabaseTooNewError
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.memory.store import MemoryStore
from app.services.memory.upgrade_copy import CopyError
from tests.fakes import OPEN_STORES, FakeEmbedder

DAY = 86_400


def user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as db:
        version: int = db.execute("PRAGMA user_version").fetchone()[0]
    return version


def contents(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as db:
        return [row[0] for row in db.execute("SELECT content FROM memories ORDER BY id")]


async def opened(path: Path) -> MemoryStore:
    store = MemoryStore(path, FakeEmbedder.DIM)
    await store.init()
    OPEN_STORES.append(store)
    return store


async def at_version(path: Path, version: int, *memories: str) -> None:
    """A file at `version` holding `memories`."""
    store = await opened(path)
    for content in memories:
        await store.insert_memory(MemoryCreate(kind=MemoryKind.fact, content=content))
    await store.close()
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"PRAGMA user_version = {version}")
        db.commit()


def aged(path: Path, seconds: float) -> Path:
    """`path` as a file last written `seconds` ago."""
    if not path.exists():
        path.write_bytes(b"")
    then = time.time() - seconds
    os.utime(path, (then, then))
    return path


async def test_a_file_behind_is_copied_as_it_was_before_its_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    await at_version(path, 3, "Likes tea")

    await (await opened(path)).close()

    copy = tmp_path / "memory.db.v3.bak"
    assert user_version(copy) == 3  # the file as it was, for the build before
    assert contents(copy) == ["Likes tea"]


async def test_a_new_file_is_not_copied(tmp_path: Path) -> None:
    """(mutation: copy whatever opens)"""
    await (await opened(tmp_path / "memory.db")).close()

    assert list(tmp_path.glob("*.bak*")) == []


async def test_a_file_already_current_is_not_copied(tmp_path: Path) -> None:
    """(mutation: copy a file that isn't behind)"""
    path = tmp_path / "memory.db"
    await at_version(path, LATEST, "Likes tea")

    await (await opened(path)).close()

    assert list(tmp_path.glob("*.bak*")) == []


async def test_the_copy_is_made_again_for_each_upgrade_that_begins(tmp_path: Path) -> None:
    """Gone back to the build before and come forward again, the copy is of the file as
    it is now, not as it was the first time (mutation: once per version)."""
    path = tmp_path / "memory.db"
    await at_version(path, 3, "First")
    await (await opened(path)).close()
    with closing(sqlite3.connect(path)) as db:  # the build before, used meanwhile
        db.execute("UPDATE memories SET content = 'Changed since'")
        db.commit()

    await (await opened(path)).close()

    assert contents(tmp_path / "memory.db.v3.bak") == ["Changed since"]


async def test_a_copy_cut_short_is_never_taken_for_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk stops the copy half way: no copy is left under its name, nor a partial
    one, and the file is as it was (mutation: copy straight to the final name)."""
    path = tmp_path / "memory.db"
    await at_version(path, 3, "Likes tea")

    async def full_disk(self: aiosqlite.Connection, target: Any, **_: Any) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(aiosqlite.Connection, "backup", full_disk)
    with pytest.raises(CopyError):
        await opened(path)

    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["memory.db"]
    assert user_version(path) == 3 and contents(path) == ["Likes tea"]


async def test_a_copy_that_doesnt_read_whole_is_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: keep the copy without checking it)"""
    path = tmp_path / "memory.db"
    await at_version(path, 3, "Likes tea")

    def damaged(_copy: Path, _version: int, _key: bytes | None) -> None:
        raise sqlite3.DatabaseError("the copy reads 'row 3 missing'")

    monkeypatch.setattr(upgrade_copy, "_check", damaged)
    with pytest.raises(CopyError):
        await opened(path)

    assert not (tmp_path / "memory.db.v3.bak").exists()


async def test_without_room_for_a_copy_there_is_no_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Said before anything is written, with what it needs (mutation: no room check)."""
    path = tmp_path / "memory.db"
    await at_version(path, 3, "Likes tea")
    monkeypatch.setattr(
        upgrade_copy.shutil,
        "disk_usage",
        lambda _folder: SimpleNamespace(total=1_000, used=990, free=10),
    )

    with pytest.raises(CopyError, match="isn't room"):
        await opened(path)

    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["memory.db"]


def test_the_room_a_copy_needs_counts_the_log_too(tmp_path: Path) -> None:
    """The check in Settings asks the same question as the upgrade (mutation: the main
    file only)."""
    path = tmp_path / "memory.db"
    assert upgrade_copy.room_needed(path) == 0
    path.write_bytes(b"x" * 1_000)
    Path(f"{path}-wal").write_bytes(b"x" * 500)
    assert upgrade_copy.room_needed(path) == 1_650


def test_a_size_needed_is_rounded_up_to_whole_mib() -> None:
    """With free space rounded down, a shortfall never reads as enough (mutation: round)."""
    mib = 1_048_576
    assert [upgrade_copy.needed_mib(size) for size in (0, 1, mib, mib + 1)] == [0, 1, 1, 2]


async def test_the_copy_holds_what_is_still_in_the_write_ahead_log(tmp_path: Path) -> None:
    """SQLite's own backup, not the file's bytes: rows not yet checkpointed live in the
    `-wal` file (mutation: copy the main file)."""
    path = tmp_path / "memory.db"
    await at_version(path, 3)
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute(
        "INSERT INTO memories (kind, content, created_at, updated_at) VALUES "
        "('fact', 'Only in the log', '2026-01-01', '2026-01-01')"
    )
    writer.commit()  # still open: nothing is checkpointed into the main file

    await (await opened(path)).close()
    writer.close()

    assert contents(tmp_path / "memory.db.v3.bak") == ["Only in the log"]


def test_copies_are_deleted_after_thirty_days(tmp_path: Path) -> None:
    """They still hold what was deleted since (mutation: never delete them). Which start
    deletes them is `app/stores.py`'s, tested with the start."""
    old = aged(tmp_path / "memory.db.v2.bak", 31 * DAY)
    recent = aged(tmp_path / "memory.db.v1.bak", 29 * DAY)

    assert upgrade_copy.remove_old(tmp_path / "memory.db") == [old]
    assert not old.exists() and recent.exists()


async def test_a_damaged_file_is_said_to_be_damaged_not_a_failed_copy(tmp_path: Path) -> None:
    """The backup copies pages as they are, so the copy of a damaged file fails its check
    too, and trying again would never help (mutation: don't check the file itself)."""
    path = tmp_path / "memory.db"
    await at_version(
        path, 3, *(f"Fact number {n}, long enough to fill pages. " * 3 for n in range(200))
    )
    damage_a_table(path, "memories")

    with pytest.raises(CopyError) as failed:
        await opened(path)

    assert failed.value.damaged
    assert user_version(path) == 3
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["memory.db"]


async def test_a_copy_that_fails_for_another_reason_is_not_called_damage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "memory.db"
    await at_version(path, 3, "Likes tea")

    def unlucky(_copy: Path, _version: int, _key: bytes | None) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(upgrade_copy, "_check", unlucky)

    with pytest.raises(CopyError) as failed:
        await opened(path)

    assert not failed.value.damaged


def damage_the_schema(path: Path) -> None:
    """Overwrite the page-type byte of the schema's page, just past the file's header:
    the version still reads, and the first statement that needs the schema fails."""
    with path.open("r+b") as file:
        file.seek(100)
        file.write(b"\xff")


def damage_a_table(path: Path, table: str) -> None:
    """Overwrite the page-type byte of `table`'s first page: the file still opens and
    reads its schema, and SQLite's check finds the damage."""
    with closing(sqlite3.connect(path)) as db:
        (root,) = db.execute(
            "SELECT rootpage FROM sqlite_master WHERE name = ?", (table,)
        ).fetchone()
        (page_size,) = db.execute("PRAGMA page_size").fetchone()
    with path.open("r+b") as file:
        file.seek((root - 1) * page_size)
        file.write(b"\xff")


def test_only_copies_named_exactly_are_ever_deleted(tmp_path: Path) -> None:
    """Someone's own files that share the name, and a name that reads as a pattern
    (mutations: a glob on the prefix; a glob with the name unescaped)."""
    theirs = [
        aged(tmp_path / name, 90 * DAY)
        for name in (
            "memory.db.v5-before-reinstall.bak",
            "memory.db.very-important.bak",
            "memory.db.v5.bak.old",
            "memory.db.bak",
            "other.db.v1.bak",
            "memory1.db.v2.bak",
        )
    ]
    (tmp_path / "memory.db.v7.bak").mkdir()
    ours = aged(tmp_path / "memory[1].db.v2.bak", 90 * DAY)

    assert upgrade_copy.remove_old(tmp_path / "memory[1].db") == [ours]
    assert upgrade_copy.remove_old(tmp_path / "memory.db") == []
    assert all(path.exists() for path in theirs) and (tmp_path / "memory.db.v7.bak").is_dir()


def test_a_partial_copy_left_behind_is_deleted_after_a_day(tmp_path: Path) -> None:
    """A fresh one may be another start's copy in progress."""
    left = aged(tmp_path / "memory.db.v3.bak.partial", 25 * 3600)
    journal = aged(tmp_path / "memory.db.v3.bak.partial-journal", 25 * 3600)
    fresh = aged(tmp_path / "memory.db.v4.bak.partial", 60)

    upgrade_copy.remove_old(tmp_path / "memory.db")

    assert not left.exists() and not journal.exists() and fresh.exists()


async def test_a_refused_file_names_the_newest_copy_this_build_opens(tmp_path: Path) -> None:
    """The copies are named for the version upgraded from, which need not be this one's
    (mutation: name the copy for this build's version)."""
    path = tmp_path / "memory.db"
    await at_version(path, LATEST + 1)
    for version in (3, 5, LATEST + 2):
        aged(tmp_path / f"memory.db.v{version}.bak", DAY)

    with pytest.raises(DatabaseTooNewError) as refused:
        await opened(path)

    assert refused.value.copy == tmp_path / "memory.db.v5.bak"
    assert "rename memory.db.v5.bak to memory.db" in str(refused.value)
    # A journal a crash left would be applied to the copy once it is named memory.db.
    assert "whose name starts with memory.db-," in str(refused.value)


async def test_a_refused_file_without_a_copy_says_so(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    await at_version(path, LATEST + 1)

    with pytest.raises(DatabaseTooNewError, match="no copy from before the update"):
        await opened(path)


async def test_the_connection_is_closed_when_opening_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Left open, its thread keeps the process from exiting (mutation: close only on a
    refusal)."""
    connections: list[aiosqlite.Connection] = []
    connect = cipher.connect

    def spied(*args: Any, **kwargs: Any) -> aiosqlite.Connection:
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(schema.cipher, "connect", spied)
    monkeypatch.setattr(schema.sqlite_vec, "loadable_path", lambda: str(tmp_path / "missing"))

    with pytest.raises(cipher.OPERATIONAL_ERRORS):
        await opened(tmp_path / "memory.db")

    assert connections and not connections[0]._running
