"""A memory file she can't use no longer stops the backend: she starts without what
lives in it, /health says what is wrong in codes the app has words for, and the chat
socket refuses in the same terms. A fault of ours still stops the start. What each
error means is `test_memory_problem.py`."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main as app_main
from app import startup
from app.adapters.embeddings.e5_embedder import EMBED_MODEL_ID
from app.core.config import get_settings
from app.main import create_app
from app.services.chat.projects import ProjectStore
from app.services.memory import cipher, migrations, upgrade_copy
from app.services.memory.migrations import LATEST, MigrationError
from tests.fakes import open_memory_file
from tests.test_upgrade_copy import damage_the_schema

DAY = 86_400


@contextmanager
def started(db: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app started on `db`."""
    monkeypatch.setenv("VERONICA_MEMORY__DB_PATH", str(db))
    get_settings.cache_clear()
    try:
        with TestClient(create_app(), base_url="http://localhost") as client:
            yield client
    finally:
        get_settings.cache_clear()


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def record(self, level: str, event: str, **fields: Any) -> None:
        self.events.append((level, event, fields))


def _at_version(db: Path, version: int) -> None:
    """A file at `version` holding one memory: something worth a copy."""
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, kind TEXT, content TEXT);"
            "INSERT INTO memories (kind, content) VALUES ('fact', 'Likes tea');"
            f"PRAGMA user_version = {version};"
        )


def _aged(path: Path, days: float) -> Path:
    path.write_bytes(b"")
    then = time.time() - days * DAY
    os.utime(path, (then, then))
    return path


def _problems(client: TestClient) -> tuple[str, list[dict[str, Any]]]:
    body = client.get("/health").json()
    return body["status"], body["problems"]


def _user_version(db: Path) -> int:
    with closing(open_memory_file(db)) as conn:
        version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    return version


def _open_connections() -> set[threading.Thread]:
    """aiosqlite's worker threads still running: one per connection not yet closed."""
    return {each for each in threading.enumerate() if "_connection_worker_thread" in each.name}


def test_a_file_from_a_newer_version_leaves_her_working_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: let the refusal stop the start, as before)"""
    db = tmp_path / "memory.db"
    _at_version(db, LATEST + 1)
    (tmp_path / "memory.db.v5.bak").write_bytes(b"")
    box = _Recorder()
    monkeypatch.setattr(startup, "current_blackbox", lambda: box)

    with started(db, monkeypatch) as client:
        status, problems = _problems(client)
        memory = client.get("/memory")
        with client.websocket_connect("ws://localhost/ws/chat") as chat:
            refusal = chat.receive_json()
            with pytest.raises(WebSocketDisconnect) as closed:
                chat.receive_json()
        # A call's mode switch must not load a memory model nothing will use.
        with pytest.raises(KeyError):
            client.app.state.voice_runtime.manager.is_loaded(EMBED_MODEL_ID)  # type: ignore[attr-defined]

    assert status == "degraded"
    assert problems == [{"code": "memory_too_new", "detail": {"copy": "memory.db.v5.bak"}}]
    assert memory.status_code == 503
    assert refusal == {"type": "error", "kind": "memory_unavailable"}
    assert closed.value.code == 1013  # try again later
    assert _user_version(db) == LATEST + 1  # never touched
    assert box.events == [
        (
            "error",
            "app.degraded",
            {
                "code": "memory_too_new",
                "error": "DatabaseTooNewError",
                "cause": None,
                "sqlite": None,
            },
        )
    ]


def test_a_damaged_file_is_said_to_be_damaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "memory.db"
    db.write_bytes(b"not a database, " * 256)

    with started(db, monkeypatch) as client:
        assert _problems(client) == ("degraded", [{"code": "memory_damaged", "detail": {}}])


def test_a_file_that_cant_be_opened_is_told_apart_from_a_damaged_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder where the file should be: nothing is wrong with the data."""
    db = tmp_path / "memory.db"
    db.mkdir()

    with started(db, monkeypatch) as client:
        assert _problems(client) == ("degraded", [{"code": "memory_unopenable", "detail": {}}])


def test_no_room_for_the_copy_says_how_much_is_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "memory.db"
    _at_version(db, 3)
    monkeypatch.setattr(
        upgrade_copy.shutil,
        "disk_usage",
        lambda _folder: SimpleNamespace(total=10**9, used=10**9 - 1, free=1),
    )

    with started(db, monkeypatch) as client:
        status, problems = _problems(client)

    assert status == "degraded"
    assert [problem["code"] for problem in problems] == ["memory_no_room"]
    assert set(problems[0]["detail"]) == {"needed_mb", "free_mb"}
    assert _user_version(db) == 3  # nothing was changed


def test_a_copy_that_fails_leaves_the_file_as_it_was_and_the_why_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The black box has the cause, which the copy's own error only wraps (mutation:
    record the error's type alone)."""
    db = tmp_path / "memory.db"
    _at_version(db, 3)
    box = _Recorder()
    monkeypatch.setattr(startup, "current_blackbox", lambda: box)

    def unlucky(_copy: Path, _version: int, _key: bytes | None) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(upgrade_copy, "_check", unlucky)

    with started(db, monkeypatch) as client:
        assert _problems(client) == ("degraded", [{"code": "memory_copy_failed", "detail": {}}])
    assert _user_version(db) == 3
    assert box.events[0][2]["cause"] == "OSError"


def test_an_upgrade_held_up_by_another_program_says_it_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read by SQLite's code, not called a failed upgrade that restarting would fix."""

    async def held(_store: Any) -> int:
        error = sqlite3.OperationalError("database is locked")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise error

    monkeypatch.setattr(migrations, "upgrade", held)

    with started(tmp_path / "memory.db", monkeypatch) as client:
        assert _problems(client) == ("degraded", [{"code": "memory_unopenable", "detail": {}}])


def test_a_fault_of_ours_in_the_upgrade_still_stops_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restarting would only fail the same way (mutation: wrap it as an upgrade that
    failed)."""

    async def faulty(_store: Any) -> int:
        raise MigrationError("a step ended its own transaction")

    monkeypatch.setattr(migrations, "upgrade", faulty)

    with pytest.raises(MigrationError), started(tmp_path / "memory.db", monkeypatch):
        pass


def test_a_failure_of_no_known_kind_still_stops_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug is not a problem the person can fix; hiding it would leave her half there."""

    async def broken(*_args: Any) -> Any:
        raise RuntimeError("a bug")

    monkeypatch.setattr(app_main, "open_stores", broken)

    with pytest.raises(RuntimeError, match="a bug"), started(tmp_path / "memory.db", monkeypatch):
        pass


def test_a_wrong_statement_of_ours_stops_the_start_rather_than_read_as_a_held_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both are OperationalError; only SQLite's code tells them apart (mutation: read
    every OperationalError as unopenable)."""

    async def wrong(store: ProjectStore) -> None:
        await store._db.execute("ALTER TABLE chat_conversations ADD COLUMN")

    monkeypatch.setattr(ProjectStore, "init_schema", wrong)
    before = _open_connections()

    with pytest.raises(cipher.OPERATIONAL_ERRORS), started(tmp_path / "memory.db", monkeypatch):
        pass

    # What was opened was closed (mutation: no clean-up): left open, a connection's
    # thread would keep the process from exiting.
    for thread in _open_connections() - before:
        thread.join(timeout=2)
    assert not _open_connections() - before


def test_a_start_that_finds_the_file_damaged_keeps_every_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One of them is the way back the notice offers (mutation: delete expired copies
    as the file opens, before it is read). Damaged in its first page, an encrypted file
    fails where SQLCipher checks the key, which can't be told from another file's key:
    the words give both ways out, a copy among them."""
    db = tmp_path / "memory.db"
    with started(db, monkeypatch):
        pass
    damage_the_schema(db)  # the version still reads: the file is refused no earlier
    expired = _aged(tmp_path / "memory.db.v5.bak", 31)

    with started(db, monkeypatch) as client:
        mismatch = {"code": "memory_locked", "detail": {"reason": "key_mismatch"}}
        assert _problems(client) == ("degraded", [mismatch])
    assert expired.exists()


def test_expired_copies_go_once_a_start_has_opened_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "memory.db"
    expired = _aged(tmp_path / "memory.db.v5.bak", 31)
    recent = _aged(tmp_path / "memory.db.v4.bak", 29)

    with started(db, monkeypatch) as client:
        assert _problems(client) == ("ok", [])

    assert not expired.exists() and recent.exists()


def test_a_healthy_start_reports_nothing_and_chat_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with started(tmp_path / "memory.db", monkeypatch) as client:
        problems = _problems(client)
        memory = client.get("/memory")
        with client.websocket_connect("ws://localhost/ws/chat") as chat:
            chat.send_json({"action": "open", "conversation_id": None, "project_id": None})
            opened = chat.receive_json()

    assert problems == ("ok", [])
    assert memory.status_code == 200
    assert opened["type"] == "opened"
