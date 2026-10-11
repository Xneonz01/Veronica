"""Her memory file encrypted in place (`services/memory/at_rest.py`) and read through its
key (`cipher.py`): everything in it comes back, nothing plain is left beside it, and a
file that can't be encrypted yet is left exactly as it was."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from app.core.event_bus import InProcessEventBus
from app.kernel.audit_integrity import AuditIntegrity
from app.kernel.capability import CapabilityResult, Decision
from app.services.chat.store import ChatStore
from app.services.memory import at_rest, cipher, migrations
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.memory.store import MemoryStore
from tests.fakes import FakeEmbedder, build_fake_kernel_runtime, build_fake_memory_runtime
from tests.test_upgrade_copy import at_version

KEY = bytes.fromhex("5a" * 32)


async def _plain_memory(folder: Path) -> Path:
    """A plain file as 0.31 left it: a memory, a document, a chat and an action logged."""
    memory = await build_fake_memory_runtime(folder, InProcessEventBus())
    kernel = await build_fake_kernel_runtime(memory, InProcessEventBus())
    await memory.service.add(MemoryCreate(kind=MemoryKind.fact, content="Sourdough baking project"))
    await memory.rag.index_text("Sourdough Guide", "Feed the sourdough starter every morning.")
    chat = ChatStore(memory.store)
    await chat.init_schema()
    conversation = await chat.create("Bread")
    await chat.add_message(conversation.id, "user", "My starter is called Clint.")
    await kernel.audit.record(
        capability="memory.save",
        actor="veronica:chat",
        decision=Decision.allowed,
        result=CapabilityResult(ok=True, detail="Saved."),
        args={"content": "User bakes sourdough."},
        duration_ms=4,
    )
    await migrations.upgrade(memory.store)
    await memory.store.close()
    return folder / "memory.db"


async def test_everything_in_it_comes_back_through_the_key(tmp_path: Path) -> None:
    """Memories and their vectors, documents and their keyword index, chats, the audit
    chain, and the version (mutation: leave the version as the export sets it, 0)."""
    path = await _plain_memory(tmp_path)
    version = migrations.LATEST

    assert at_rest.encrypt(path, KEY) == 1

    assert at_rest.holds_encrypted(path) and not cipher.plain(path)
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus(), key=KEY)
    kernel = await build_fake_kernel_runtime(memory, InProcessEventBus())
    recalled = await memory.retriever.retrieve("sourdough project")
    assert [item.memory.content for item in recalled.items] == ["Sourdough baking project"]
    assert (await memory.rag.retrieve("starter"))[0].document_title == "Sourdough Guide"
    chat = ChatStore(memory.store)
    assert [m.content for m in await chat.messages((await chat.list())[0].id)] == [
        "My starter is called Clint."
    ]
    integrity = AuditIntegrity(memory.store)
    await integrity.init_schema()
    assert (await integrity.verify()).ok and await kernel.audit.count() == 1
    async with memory.store.db.execute("PRAGMA user_version") as cur:
        assert (await cur.fetchone())[0] == version  # type: ignore[index]
    await memory.store.close()


def test_the_encrypted_file_reads_as_nothing_without_its_key(tmp_path: Path) -> None:
    """Not with SQLite as it comes, nor with another key (mutation: no key set)."""
    path = tmp_path / "memory.db"
    with closing(cipher.open_sync(path, KEY)) as db:
        db.execute("CREATE TABLE notes (text)")
        db.execute("INSERT INTO notes VALUES ('Rowan keeps bees')")
        db.commit()

    with pytest.raises(sqlite3.DatabaseError), closing(sqlite3.connect(path)) as plain:
        plain.execute("SELECT * FROM notes")
    with pytest.raises(cipher.SQLITE_ERRORS), closing(cipher.open_sync(path, bytes(32))) as other:
        other.execute("SELECT * FROM notes")
    assert b"Rowan keeps bees" not in path.read_bytes()


async def test_the_copies_from_before_upgrades_are_encrypted_too(tmp_path: Path) -> None:
    """No plain copy of her memory left beside the encrypted one (mutation: the file
    alone)."""
    path = await _plain_memory(tmp_path)
    copy = tmp_path / "memory.db.v5.bak"
    copy.write_bytes(path.read_bytes())

    assert at_rest.encrypt(path, KEY) == 2

    assert not cipher.plain(copy)
    with closing(cipher.open_sync(copy, KEY)) as db:
        assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


async def test_a_file_encrypted_already_is_left_alone(tmp_path: Path) -> None:
    path = await _plain_memory(tmp_path)
    at_rest.encrypt(path, KEY)
    before = path.read_bytes()

    assert at_rest.encrypt(path, KEY) == 0

    assert path.read_bytes() == before


def test_a_newer_builds_file_is_left_as_it_is(tmp_path: Path) -> None:
    """It is refused before anything touches it (mutation: encrypt any plain file)."""
    path = tmp_path / "memory.db"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(f"CREATE TABLE t (a); PRAGMA user_version = {migrations.LATEST + 1};")
    before = path.read_bytes()

    assert at_rest.encrypt(path, KEY) == 0

    assert path.read_bytes() == before


async def test_an_encryption_cut_short_is_cleared_and_done_again(tmp_path: Path) -> None:
    path = await _plain_memory(tmp_path)
    (tmp_path / "memory.db.encrypting").write_bytes(b"half of it")

    assert at_rest.encrypt(path, KEY) == 1

    assert not (tmp_path / "memory.db.encrypting").exists() and not cipher.plain(path)


async def test_a_file_written_to_meanwhile_is_left_plain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write since the export would be lost with the plain file (mutation: swap it in
    all the same)."""
    path = await _plain_memory(tmp_path)
    before = path.read_bytes()
    stamps = iter([(1, 1), (1, 2)])
    monkeypatch.setattr(at_rest, "_stamp", lambda _file: next(stamps))

    with pytest.raises(at_rest.NotEncrypted, match="in use"):
        at_rest.encrypt(path, KEY)

    assert path.read_bytes() == before
    assert not (tmp_path / "memory.db.encrypting").exists()


async def test_a_copy_that_doesnt_read_back_whole_is_not_swapped_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: swap it in without the check)"""
    path = await _plain_memory(tmp_path)
    before = path.read_bytes()
    counted = at_rest._rows
    calls: list[Any] = []

    def one_row_short(db: Any) -> dict[str, int]:
        rows = counted(db)
        calls.append(db)
        if len(calls) == 2:  # the encrypted copy's
            rows["memories"] -= 1
        return rows

    monkeypatch.setattr(at_rest, "_rows", one_row_short)

    with pytest.raises(at_rest.NotEncrypted):
        at_rest.encrypt(path, KEY)

    assert path.read_bytes() == before
    assert not (tmp_path / "memory.db.encrypting").exists()


async def test_no_room_leaves_it_plain_and_says_how_much_is_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = await _plain_memory(tmp_path)
    monkeypatch.setattr(at_rest.shutil, "disk_usage", lambda _p: _Usage(0))

    with pytest.raises(at_rest.NotEncrypted) as refused:
        at_rest.encrypt(path, KEY)

    assert refused.value.needed_mb is not None and refused.value.needed_mb >= 1
    assert refused.value.free_mb == 0 and cipher.plain(path)


class _Usage:
    def __init__(self, free: int) -> None:
        self.free = free


def test_an_encrypted_file_is_told_from_a_damaged_one(tmp_path: Path) -> None:
    """A damaged plain file, its header gone, reads as damaged, not as a memory whose
    key is missing (mutations: any file without a header is encrypted; judge a file of
    less than a page)."""
    encrypted = tmp_path / "encrypted.db"
    with closing(cipher.open_sync(encrypted, KEY)) as db:
        db.execute("CREATE TABLE t (a)")
        db.commit()
    text = tmp_path / "text.db"
    text.write_bytes(b"not a database, " * 256)
    zeroed = tmp_path / "zeroed.db"
    with closing(sqlite3.connect(zeroed)) as db:
        db.execute("CREATE TABLE t (a)")
    zeroed.write_bytes(bytes(16) + zeroed.read_bytes()[16:])
    small = tmp_path / "small.db"
    small.write_bytes(os.urandom(512))

    assert at_rest.holds_encrypted(encrypted)
    for other in (text, zeroed, small, tmp_path / "absent.db"):
        assert not at_rest.holds_encrypted(other), other.name


async def test_the_copy_an_upgrade_keeps_is_encrypted_with_its_key(tmp_path: Path) -> None:
    """The way back stays hers alone (mutation: back up into a plain copy)."""
    path = tmp_path / "memory.db"
    await at_version(path, 3, "Likes tea")
    at_rest.encrypt(path, KEY)

    store = MemoryStore(path, FakeEmbedder.DIM, key=KEY)
    await store.init()
    await store.close()

    copy = tmp_path / "memory.db.v3.bak"
    assert at_rest.holds_encrypted(copy)
    with closing(cipher.open_sync(copy, KEY)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT content FROM memories").fetchall() == [("Likes tea",)]
