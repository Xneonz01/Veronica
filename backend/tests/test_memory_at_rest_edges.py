"""Her memory's encryption at rest at its edges (`services/memory/at_rest.py`): a rename a
FAT disk cut in two, a copy that won't encrypt, a copy's age, a file in use, and a key
that isn't the file's. Found by the review of 0.32.0."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from app.core.data_key import Locked
from app.services.memory import at_rest, cipher

KEY = bytes.fromhex("5a" * 32)
DAY = 86_400


def _plain(path: Path, note: str = "Rowan keeps bees") -> Path:
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE notes (text)")
        db.execute("INSERT INTO notes VALUES (?)", (note,))
        db.commit()
    return path


def _encrypted(path: Path, note: str = "Rowan keeps bees") -> Path:
    with closing(cipher.open_sync(path, KEY)) as db:
        db.execute("CREATE TABLE notes (text)")
        db.execute("INSERT INTO notes VALUES (?)", (note,))
        db.commit()
    return path


def _notes(path: Path) -> list[str]:
    with closing(cipher.open_sync(path, KEY)) as db:
        return [text for (text,) in db.execute("SELECT text FROM notes")]


def test_a_rename_a_fat_disk_cut_in_two_is_finished(tmp_path: Path) -> None:
    """The old file went first, and the encrypted one, checked before, was all that was
    left; the next start deleted it and began an empty memory (mutation: clear leftovers
    whether or not the file is there)."""
    path = tmp_path / "memory.db"
    _encrypted(tmp_path / "memory.db.encrypting")

    at_rest.encrypt(path, KEY)

    assert _notes(path) == ["Rowan keeps bees"]
    assert not (tmp_path / "memory.db.encrypting").exists()


def test_what_isnt_whole_is_not_put_in_its_place_or_deleted(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    left = tmp_path / "memory.db.encrypting"
    left.write_bytes(os.urandom(8192))

    at_rest.encrypt(path, KEY)

    assert not path.exists() and left.exists()


def test_what_a_cut_rename_left_counts_as_encrypted(tmp_path: Path) -> None:
    """A new key would lock it away (mutation: look at her memory file alone)."""
    _encrypted(tmp_path / "memory.db.encrypting")

    assert at_rest.encrypted_anything(tmp_path / "memory.db")


def test_an_encrypted_copy_counts_though_her_file_is_plain(tmp_path: Path) -> None:
    """(mutation: leave the copies out)"""
    path = _plain(tmp_path / "memory.db")
    _encrypted(tmp_path / "memory.db.v5.bak")

    assert at_rest.encrypted_anything(path)


def test_a_copy_that_wont_encrypt_is_said_after_the_others_are_done(tmp_path: Path) -> None:
    """One damaged copy kept every copy after it plain, and said her memory file was
    (mutation: stop at the first copy that fails)."""
    path = _plain(tmp_path / "memory.db")
    damaged = tmp_path / "memory.db.v4.bak"
    damaged.write_bytes(b"SQLite format 3\x00" + bytes(84) + b"not pages" * 1000)
    later = _plain(tmp_path / "memory.db.v5.bak")

    with pytest.raises(at_rest.NotEncrypted) as refused:
        at_rest.encrypt(path, KEY)

    assert refused.value.copy == "memory.db.v4.bak"
    assert _notes(path) == ["Rowan keeps bees"] and _notes(later) == ["Rowan keeps bees"]
    assert cipher.plain(damaged)


def test_her_memory_file_that_wont_encrypt_is_said_at_once(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    path.write_bytes(b"SQLite format 3\x00" + bytes(84) + b"not pages" * 1000)

    with pytest.raises(at_rest.NotEncrypted) as refused:
        at_rest.encrypt(path, KEY)

    assert refused.value.copy is None


def test_an_encrypted_copy_keeps_its_age(tmp_path: Path) -> None:
    """Its 30 days count from when it was made: encrypted, a copy of 29 days had 30 more
    (mutation: leave the new file's time)."""
    path = _plain(tmp_path / "memory.db")
    copy = _plain(tmp_path / "memory.db.v5.bak")
    made = time.time() - 29 * DAY
    os.utime(copy, (made, made))
    before = copy.stat().st_mtime_ns

    at_rest.encrypt(path, KEY)

    assert at_rest.holds_encrypted(copy) and copy.stat().st_mtime_ns == before


def test_a_file_another_program_began_writing_is_left_plain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its journal, begun during the export, would be played into the encrypted file at
    its next open (mutation: no look for it)."""
    path = _plain(tmp_path / "memory.db")
    before = path.read_bytes()
    checked = at_rest._check

    def and_a_write_begins(*args: Any) -> None:
        checked(*args)
        (tmp_path / "memory.db-journal").write_bytes(b"a transaction another program began")

    monkeypatch.setattr(at_rest, "_check", and_a_write_begins)

    with pytest.raises(at_rest.NotEncrypted, match="in use"):
        at_rest.encrypt(path, KEY)

    assert path.read_bytes() == before


def test_a_leftover_that_cant_be_removed_doesnt_hide_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Held by a scan, its removal raised in place of `NotEncrypted`, and she started
    without her memory rather than with it plain (mutation: let the removal's error
    through)."""
    path = _plain(tmp_path / "memory.db")
    monkeypatch.setattr(at_rest, "_check", _never_whole)
    unlink = Path.unlink
    tries: list[Path] = []

    def held(file: Path, missing_ok: bool = False) -> None:
        if file.name == "memory.db.encrypting" and file.exists():
            tries.append(file)
            raise PermissionError(32, "The process cannot access the file")
        unlink(file, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held)

    with pytest.raises(at_rest.NotEncrypted):
        at_rest.encrypt(path, KEY)

    assert tries and cipher.plain(path)


def _never_whole(*_args: Any) -> None:
    raise sqlite3.DatabaseError("the encrypted copy reads 'damaged'")


def test_a_key_that_isnt_the_files_is_told_from_one_that_is(tmp_path: Path) -> None:
    """It read as a damaged file, whose words point to copies the same key can't open
    either (mutation: no look)."""
    path = _encrypted(tmp_path / "memory.db")

    with pytest.raises(Locked) as locked:
        at_rest.check_key(path, bytes(32))

    assert locked.value.reason == "key_mismatch"
    at_rest.check_key(path, KEY)
    at_rest.check_key(_plain(tmp_path / "plain.db"), KEY)
    assert not (tmp_path / "absent.db").exists()
    at_rest.check_key(tmp_path / "absent.db", KEY)
    assert not (tmp_path / "absent.db").exists()
