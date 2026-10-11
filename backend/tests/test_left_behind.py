"""Her memory and voices where versions before 0.29.0 kept them, while the settings now
keep them elsewhere: named at every start, never moved, and her memory never started a
second time, empty, at the new place (`app/left_behind.py`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import left_behind
from app.core import data_acl
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def earlier(tmp_path: Path, as_set_up: pytest.MonkeyPatch) -> Path:
    """The installation's data folder as an earlier version left it: her memory, her
    voices, and her diagnostics."""
    folder = tmp_path / "install" / "data"
    for name in ("memory", "voices", "metrics", "telemetry"):
        (folder / name).mkdir(parents=True)
    (folder / "memory" / "memory.db").write_bytes(b"hers")
    (folder / "voices" / "voices.json").write_text("[]", encoding="utf-8")
    (folder / "metrics" / "voice_turns.jsonl").write_bytes(b"")
    as_set_up.setattr(left_behind, "EARLIER", folder)
    return folder


def test_what_was_left_is_named_at_every_start_and_never_moved(
    tmp_path: Path, earlier: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Copies included, and never the diagnostics, which just start afresh (mutations:
    name them once only; name the diagnostics; move them)."""
    moved = Settings(data_dir=str(tmp_path / "moved"))

    assert left_behind.warn(moved) == ["memory", "voices"]
    assert "still holds her memory and voices" in caplog.text
    assert "stop her backend and move each folder whole" in caplog.text
    assert (earlier / "memory" / "memory.db").read_bytes() == b"hers"

    (tmp_path / "moved" / "memory").mkdir(parents=True)
    (tmp_path / "moved" / "memory" / "memory.db").write_bytes(b"copied")
    assert left_behind.left_behind(moved) == ["memory", "voices"]  # a copy is still hers

    (earlier / "memory" / "memory.db").unlink()
    assert left_behind.left_behind(moved) == ["voices"]  # the memory was moved over


def test_a_folder_emptied_by_the_move_counts_as_moved(tmp_path: Path, earlier: Path) -> None:
    """(mutation: judge by the folder, which the move leaves behind)"""
    (earlier / "voices" / "voices.json").unlink()

    assert left_behind.left_behind(Settings(data_dir=str(tmp_path / "moved"))) == ["memory"]


def test_the_installations_own_folder_named_another_way_is_no_move(earlier: Path) -> None:
    """(mutation: compare the paths as spelled)"""
    same = Settings(data_dir=str(earlier / "memory" / ".." / ".." / "data"))

    assert left_behind.left_behind(same) == []
    assert not left_behind.memory_left_behind(same)


def test_her_memory_is_not_opened_a_second_time_at_the_new_place(
    tmp_path: Path, earlier: Path
) -> None:
    """Only while nothing is there yet: a memory moved or copied over is hers to open
    (mutation: refuse whenever one is left behind)."""
    moved = Settings(data_dir=str(tmp_path / "moved"))
    assert left_behind.memory_left_behind(moved)

    (tmp_path / "moved" / "memory").mkdir(parents=True)
    (tmp_path / "moved" / "memory" / "memory.db").write_bytes(b"moved")
    assert not left_behind.memory_left_behind(moved)


def test_a_folder_closed_to_her_is_left_unjudged(
    tmp_path: Path, earlier: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a report: it must never stop a start (mutation: let the error through)."""

    def closed(self: Path, *, follow_symlinks: bool = True) -> bool:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "exists", closed)
    moved = Settings(data_dir=str(tmp_path / "moved"))

    assert left_behind.left_behind(moved) == []
    assert not left_behind.memory_left_behind(moved)


def test_a_start_with_her_memory_left_behind_goes_on_without_it(
    tmp_path: Path, earlier: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And says why, rather than start an empty memory that moving hers back would
    overwrite (mutation: open the stores anyway)."""

    async def keep_private(_path: Path) -> data_acl.Kept:
        return data_acl.Kept("ok")

    monkeypatch.setattr(data_acl, "keep_private", keep_private)
    moved = tmp_path / "moved"

    with TestClient(
        create_app(Settings(data_dir=str(moved))), base_url="http://localhost"
    ) as client:
        health = client.get("/health").json()

    assert health["status"] == "degraded"
    assert {"code": "memory_left_behind", "detail": {}} in health["problems"]
    assert not (moved / "memory").exists()
    assert (earlier / "memory" / "memory.db").read_bytes() == b"hers"


def test_a_new_place_closed_to_her_is_opened_to_say_so(
    tmp_path: Path, earlier: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether her memory is there can't be told, so opening it says what's wrong, not
    this (mutation: take what can't be seen for nothing there)."""
    moved = tmp_path / "moved"
    exists = Path.exists

    def closed_there(self: Path, *, follow_symlinks: bool = True) -> bool:
        if self.is_relative_to(moved):
            raise PermissionError(13, "Access is denied")
        return exists(self)

    monkeypatch.setattr(Path, "exists", closed_there)

    assert not left_behind.memory_left_behind(Settings(data_dir=str(moved)))
