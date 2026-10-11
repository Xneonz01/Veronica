"""The data key (`core/data_key.py`): made once, kept only as Windows wrapped it, and
never made again over something encrypted. Real DPAPI: these run on Windows."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from app.core import data_key, dpapi
from app.core.data_key import KEY_BYTES, Locked, key_path, open_key

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows service")


def _written(path: Path, blob: bytes) -> None:
    path.write_text(json.dumps({"format": 1, "dpapi": base64.b64encode(blob).decode("ascii")}))


def test_the_key_lives_beside_what_it_opens() -> None:
    assert key_path(Path("data/memory/memory.db")) == Path("data/memory/memory.key")


def test_a_key_is_made_once_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "memory.key"

    made = open_key(path, encrypted=False)

    assert len(made) == KEY_BYTES and open_key(path, encrypted=True) == made


def test_the_key_file_holds_no_key_in_the_clear(tmp_path: Path) -> None:
    """Only as Windows wrapped it (mutation: store the key as it is)."""
    path = tmp_path / "memory.key"
    key = open_key(path, encrypted=False)
    stored = path.read_bytes()

    for form in (key, key.hex().encode(), base64.b64encode(key)):
        assert form not in stored


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        (None, "key_missing"),
        (b"{not a key file", "key_damaged"),
        (b'{"format": 9, "dpapi": ""}', "key_damaged"),
        (b'{"format": 1, "dpapi": ""}', "key_damaged"),  # read as another account's
        (b'{"format": 1, "dpapi": "cm93YW4="}', "key_damaged"),
    ],
)
def test_with_something_encrypted_a_key_it_cant_use_locks_it(
    tmp_path: Path, contents: bytes | None, reason: str
) -> None:
    """Nothing is made or written over: a new key would lose what is encrypted for good
    (mutations: make one anyway; set the old one aside)."""
    path = tmp_path / "memory.key"
    if contents is not None:
        path.write_bytes(contents)

    with pytest.raises(Locked) as locked:
        open_key(path, encrypted=True)

    assert locked.value.reason == reason
    assert (path.read_bytes() if path.exists() else None) == contents
    assert [entry.name for entry in tmp_path.iterdir()] == ([path.name] if contents else [])


def test_a_key_another_account_wrapped_is_that_accounts(tmp_path: Path) -> None:
    """Windows won't unwrap it here: the words say which account to use (mutation: call
    it damaged)."""
    path = tmp_path / "memory.key"
    _written(path, dpapi.protect(bytes(KEY_BYTES), b"another account's"))

    with pytest.raises(Locked) as locked:
        open_key(path, encrypted=True)

    assert locked.value.reason == "another_account"


def test_with_nothing_encrypted_an_unusable_key_is_set_aside_for_a_new_one(tmp_path: Path) -> None:
    """It locks nothing, so a new key loses nothing; kept, not deleted, all the same
    (mutation: refuse to start)."""
    path = tmp_path / "memory.key"
    path.write_bytes(b"{not a key file")

    made = open_key(path, encrypted=False)

    assert open_key(path, encrypted=True) == made
    (aside,) = tmp_path.glob("memory.key.unusable-*")
    assert aside.read_bytes() == b"{not a key file"


def test_a_key_made_meanwhile_is_the_one_used(tmp_path: Path) -> None:
    """By something not holding the lock (two starts at once take turns on it,
    `test_encrypted_start`): the one found is used, never written over (mutation: write
    over it)."""
    path = tmp_path / "memory.key"
    first = open_key(path, encrypted=False)

    assert data_key._create(path) == first
    assert open_key(path, encrypted=True) == first


def test_where_there_is_no_dpapi_there_is_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing to keep one with: what is plain stays plain, and what is encrypted can't
    be opened there (mutation: ask DPAPI all the same)."""
    monkeypatch.setattr(dpapi, "available", lambda: False)
    path = tmp_path / "memory.key"

    assert open_key(path, encrypted=False) is None and not path.exists()
    with pytest.raises(Locked) as locked:
        open_key(path, encrypted=True)
    assert locked.value.reason == "another_account"
