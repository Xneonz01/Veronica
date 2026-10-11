"""A start and her encrypted memory (ADR 0001): a plain file encrypted and still hers, a
key she can't use reported as a lock with nothing written over, and a file that can't be
encrypted yet used as it is. Real DPAPI: these run on Windows."""

from __future__ import annotations

import base64
import json
import sys
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import data_key, dpapi
from app.core.data_key import key_path
from app.core.event_bus import InProcessEventBus
from app.core.file_lock import held, lock_path
from app.services.memory import at_rest, cipher
from app.services.memory.models import MemoryCreate, MemoryKind
from app.stores import unlock
from tests.fakes import build_fake_memory_runtime
from tests.test_degraded_start import started

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows service")


async def _plain_memory(folder: Path) -> Path:
    memory = await build_fake_memory_runtime(folder, InProcessEventBus())
    await memory.service.add(MemoryCreate(kind=MemoryKind.fact, content="Rowan keeps bees"))
    await memory.store.close()
    return folder / "memory.db"


def _problems(client: TestClient) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = client.get("/health").json()["problems"]
    return problems


async def test_a_start_encrypts_a_plain_file_and_she_keeps_all_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutations: open it plain; make no key; encrypt it after opening it)"""
    db = await _plain_memory(tmp_path)

    with started(db, monkeypatch) as client:
        problems = _problems(client)
        memories = client.get("/memory").json()["memories"]

    assert problems == []
    assert [m["content"] for m in memories] == ["Rowan keeps bees"]
    assert at_rest.holds_encrypted(db) and key_path(db).is_file()
    assert b"Rowan keeps bees" not in db.read_bytes()


async def test_a_key_another_account_wrapped_locks_her_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """She starts without it and says which account opens it (mutation: make a new key
    over it)."""
    db = tmp_path / "memory.db"
    with closing(cipher.open_sync(db, bytes(32))) as conn:
        conn.execute("CREATE TABLE t (a)")
        conn.commit()
    blob = dpapi.protect(bytes(32), b"another account's")
    wrapped = json.dumps({"format": 1, "dpapi": base64.b64encode(blob).decode("ascii")})
    key_path(db).write_text(wrapped, encoding="utf-8")
    before = db.read_bytes()

    with started(db, monkeypatch) as client:
        problems = _problems(client)
        memory = client.get("/memory")

    assert problems == [{"code": "memory_locked", "detail": {"reason": "another_account"}}]
    assert memory.status_code == 503
    assert db.read_bytes() == before and key_path(db).read_text(encoding="utf-8") == wrapped


async def test_encrypted_memory_without_its_key_is_locked_and_no_key_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new key would lock what is there away for good (mutation: make one)."""
    db = tmp_path / "memory.db"
    with closing(cipher.open_sync(db, bytes(32))) as conn:
        conn.execute("CREATE TABLE t (a)")
        conn.commit()

    with started(db, monkeypatch) as client:
        problems = _problems(client)

    assert problems == [{"code": "memory_locked", "detail": {"reason": "key_missing"}}]
    assert not key_path(db).exists()


async def test_a_file_that_cant_be_encrypted_yet_is_used_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Said, with the room it needs, and her memory still there (mutations: stop the
    start; open it with the key all the same)."""
    db = await _plain_memory(tmp_path)

    def no_room(_path: Path, _key: bytes) -> int:
        raise at_rest.NotEncrypted("no room", needed_mb=12, free_mb=3)

    monkeypatch.setattr(at_rest, "encrypt", no_room)

    with started(db, monkeypatch) as client:
        problems = _problems(client)
        memories = client.get("/memory").json()["memories"]

    assert problems == [{"code": "memory_not_encrypted", "detail": {"needed_mb": 12, "free_mb": 3}}]
    assert [m["content"] for m in memories] == ["Rowan keeps bees"]
    assert cipher.plain(db)


async def test_a_key_that_doesnt_open_her_file_is_said_as_that(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another memory's key beside it read as a damaged file, whose words never mention
    the key (mutation: no look before the file is opened)."""
    db = tmp_path / "memory.db"
    with closing(cipher.open_sync(db, bytes(32))) as conn:
        conn.execute("CREATE TABLE t (a)")
        conn.commit()
    blob = dpapi.protect(bytes.fromhex("7e" * 32), data_key._ENTROPY)
    wrapped = json.dumps({"format": 1, "dpapi": base64.b64encode(blob).decode("ascii")})
    key_path(db).write_text(wrapped, encoding="utf-8")
    before = db.read_bytes()

    with started(db, monkeypatch) as client:
        problems = _problems(client)

    assert problems == [{"code": "memory_locked", "detail": {"reason": "key_mismatch"}}]
    assert db.read_bytes() == before


async def test_where_there_is_no_dpapi_her_memory_stays_plain_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every start there raised, and she started without her memory (mutation: ask
    DPAPI all the same)."""
    db = await _plain_memory(tmp_path)
    monkeypatch.setattr(dpapi, "available", lambda: False)

    with started(db, monkeypatch) as client:
        problems = _problems(client)
        memories = client.get("/memory").json()["memories"]

    assert {"code": "memory_not_encrypted", "detail": {"reason": "unsupported"}} in problems
    assert [m["content"] for m in memories] == ["Rowan keeps bees"]
    assert cipher.plain(db) and not key_path(db).exists()


async def test_a_copy_that_couldnt_be_encrypted_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = await _plain_memory(tmp_path)

    def one_copy_left(_path: Path, _key: bytes) -> int:
        raise at_rest.NotEncrypted("damaged", copy="memory.db.v4.bak")

    monkeypatch.setattr(at_rest, "encrypt", one_copy_left)

    with started(db, monkeypatch) as client:
        problems = _problems(client)

    assert problems == [{"code": "memory_not_encrypted", "detail": {"copy": "memory.db.v4.bak"}}]


async def test_a_second_start_waits_for_the_first_to_finish_with_the_key(tmp_path: Path) -> None:
    """Two starts at once: the second read the first's key file before it was written,
    set it aside as damaged and made its own, and the first encrypted her memory with
    the key set aside (mutation: open the key without the lock)."""
    db = await _plain_memory(tmp_path)
    done: list[bytes | None] = []

    def second_start() -> None:
        key, _ = unlock(db)
        done.append(key)

    with held(lock_path(key_path(db))):
        second = threading.Thread(target=second_start)
        second.start()
        second.join(0.3)
        assert second.is_alive() and not key_path(db).exists()
    second.join(10)

    assert done and done[0] is not None and at_rest.holds_encrypted(db)
    assert list(tmp_path.glob("memory.key.unusable-*")) == []
