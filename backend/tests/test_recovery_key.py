"""Her recovery key and its sealing in a key file (`core/recovery_key.py`,
`core/data_key.py`): read back however it is written, a typo or a swap caught before it
is tried, every key file sealed or none, and a key file opened for this account only by
the right one. Real DPAPI for the key files: those run on Windows."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

from app.core import data_key, dpapi, recovery_key
from app.core.data_key import (
    KEY_BYTES,
    Locked,
    Recovery,
    RecoveryMark,
    open_key,
    recover,
    recovery_mark,
    seal_recovery,
)
from app.core.recovery_key import NotARecoveryKey
from app.core.sealed import SealBroken, seal, unseal

windows = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows service")
MADE = "2026-09-26T12:00:00+00:00"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def test_a_recovery_key_is_written_in_nine_groups_of_four() -> None:
    """Crockford's base 32, big-endian (checked by hand for the first five bytes), then
    its CRC-20."""
    written = recovery_key.written(bytes(range(20)))

    assert written == "000G-40R4-0M30-E209-185G-R38E-1W81-24GK-AT36"


def test_it_reads_back_however_it_is_spaced_split_or_cased() -> None:
    """I and L read as 1, O as 0: the characters Crockford's alphabet leaves out."""
    secret = bytes(range(20))
    written = recovery_key.written(secret)

    for text in (
        written,
        written.lower(),
        written.replace("-", ""),
        written.replace("-", " "),
        f"  {written}\n",
        written.replace("1", "I").replace("0", "O"),
        written.replace("1", "l").replace("0", "o"),
        written.replace("-", "\u2013"),  # an en dash, as a word processor writes one
    ):
        assert recovery_key.read(text) == secret, text


# Keys spread over the space, and one a 20-bit hash can't guard: changing its fifth
# character from 0 to 1 leaves a SHA-256 check the same (found by search, 1 key in 2,444).
KEYS = [
    *(bytes((seed * 37 + at * 101 + 7) % 256 for at in range(20)) for seed in range(20)),
    bytes.fromhex("5fdb400b2cd634590fd18eb5a345f15ef2e3b4dc"),
]


@pytest.mark.parametrize("secret", KEYS)
def test_any_character_typed_wrong_or_two_swapped_is_caught(secret: bytes) -> None:
    """For every key, not most: a hash of the same size let one change through in about
    1 key of 1,000, so this test failed now and then (mutations: no check; a hash)."""
    compact = recovery_key.written(secret).replace("-", "")
    changed = [
        compact[:at] + other + compact[at + 1 :]
        for at, typed in enumerate(compact)
        for other in ALPHABET
        if other != typed
    ]
    swapped = [
        compact[:at] + compact[at + 1] + compact[at] + compact[at + 2 :]
        for at in range(len(compact) - 1)
        if compact[at] != compact[at + 1]
    ]
    passed = []
    for text in changed + swapped:
        try:
            recovery_key.read(text)
        except NotARecoveryKey:
            continue
        passed.append(text)

    assert passed == []


@pytest.mark.parametrize(
    "text",
    ["", "0000", "0000-0000-0000-0000-0000-0000-0000-0000-VS3", "U" * 36, "0000-" * 9 + "VS3W"],
)
def test_text_that_isnt_a_recovery_key_is_refused(text: str) -> None:
    with pytest.raises(NotARecoveryKey):
        recovery_key.read(text)


def test_what_it_derives_never_changes() -> None:
    """Pinned, as HKDF-SHA256 with the fixed info strings gives them (checked with a
    plain HMAC): a change would leave every recovery key made unable to open what it
    sealed, or to be known as the one that did."""
    assert recovery_key.sealing_key(bytes(20)).hex() == (
        "56491862e91ea0cd4d73fcf80c38adb738819148f6be0e69a21e8d87ed269e98"
    )
    assert recovery_key.key_id(bytes(20)) == "60473c1f739023aa"
    assert recovery_key.sealing_key(b"\x01" * 20) != recovery_key.sealing_key(bytes(20))


def test_a_sealed_blob_cut_short_is_a_broken_seal() -> None:
    """It raised the cipher's own error, a 500 where it should say the key doesn't open
    it (mutation: no length check)."""
    key = os.urandom(32)
    blob = seal(b"clip", key, "rowan.wav")
    for cut in (5, 12, 20, 32):
        with pytest.raises(SealBroken):
            unseal(blob[:cut], key, "rowan.wav")


def _key_file(path: Path, key: bytes) -> None:
    """A key file wrapping `key` for this account."""
    blob = dpapi.protect(key, data_key._ENTROPY)
    path.write_text(
        json.dumps({"format": 1, "dpapi": base64.b64encode(blob).decode("ascii")}),
        encoding="utf-8",
    )


def _moved_to_another_account(path: Path) -> None:
    """The key file as another Windows account finds it: its sealing as it was, its
    wrapping one this account can't unwrap."""
    stored = json.loads(path.read_text(encoding="utf-8"))
    key = open_key(path, encrypted=True)
    assert key is not None
    stored["dpapi"] = base64.b64encode(dpapi.protect(key, b"another account's")).decode("ascii")
    path.write_text(json.dumps(stored), encoding="utf-8")


def _sealing() -> bytes:
    return recovery_key.sealing_key(recovery_key.new())


@windows
def test_the_recovery_sealing_is_kept_beside_the_dpapi_wrapping(tmp_path: Path) -> None:
    """The key still opens as before, never in the clear (mutation: write the key
    as it is)."""
    path = tmp_path / "memory.key"
    key = open_key(path, encrypted=False)
    assert key is not None

    seal_recovery({path: key}, _sealing(), "id-1", MADE)

    assert open_key(path, encrypted=True) == key
    assert recovery_mark(path) == RecoveryMark("id-1", MADE)
    stored = path.read_bytes()
    for form in (key, key.hex().encode(), base64.b64encode(key)):
        assert form not in stored
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["memory.key"]


@windows
def test_every_key_file_is_sealed_or_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second file held by a reader (Windows refuses the rename): the first was left
    sealed with a key nobody was shown (mutations: put each in place as it is written;
    no putting back)."""
    memory, voices = tmp_path / "memory.key", tmp_path / "voices.key"
    keys = {memory: os.urandom(KEY_BYTES), voices: os.urandom(KEY_BYTES)}
    for path, key in keys.items():
        _key_file(path, key)
    before = {path: path.read_bytes() for path in keys}
    replace = data_key.os.replace

    def held(source: object, target: object) -> None:
        if Path(str(target)) == voices:
            raise PermissionError(5, "Access is denied")
        replace(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(data_key.os, "replace", held)
    with pytest.raises(PermissionError):
        seal_recovery(keys, _sealing(), "id-1", MADE)

    assert {path: path.read_bytes() for path in keys} == before
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["memory.key", "voices.key"]


@windows
def test_a_key_file_that_no_longer_wraps_the_key_is_not_sealed(tmp_path: Path) -> None:
    """`recover` trusts the sealing enough to write it over a wrapping it can't open, so
    it must be the key the file wraps (mutation: seal whatever is given)."""
    path = tmp_path / "memory.key"
    _key_file(path, os.urandom(KEY_BYTES))
    before = path.read_bytes()

    with pytest.raises(Locked) as locked:
        seal_recovery({path: os.urandom(KEY_BYTES)}, _sealing(), "id-1", MADE)

    assert locked.value.reason == "key_mismatch" and path.read_bytes() == before


@windows
def test_the_right_recovery_key_wraps_another_accounts_key_for_this_one(tmp_path: Path) -> None:
    """(mutation: open it and save nothing)"""
    path = tmp_path / "memory.key"
    key = os.urandom(KEY_BYTES)
    _key_file(path, key)
    sealing = _sealing()
    seal_recovery({path: key}, sealing, "id-1", MADE)
    _moved_to_another_account(path)
    with pytest.raises(Locked) as locked:
        open_key(path, encrypted=True)
    assert locked.value.reason == "another_account" and locked.value.recoverable

    assert recover(path, sealing) is Recovery.RESTORED

    assert open_key(path, encrypted=True) == key
    assert recovery_mark(path) == RecoveryMark("id-1", MADE)
    assert recover(path, sealing) is Recovery.OPEN


@windows
def test_the_wrong_recovery_key_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "memory.key"
    key = os.urandom(KEY_BYTES)
    _key_file(path, key)
    seal_recovery({path: key}, _sealing(), "id-1", MADE)
    _moved_to_another_account(path)
    before = path.read_bytes()

    assert recover(path, _sealing()) is Recovery.WRONG
    assert path.read_bytes() == before


@windows
def test_a_key_file_without_a_recovery_sealing_says_so(tmp_path: Path) -> None:
    """Locked, and not one a recovery key can open (mutation: always recoverable)."""
    path = tmp_path / "memory.key"
    _key_file(path, os.urandom(KEY_BYTES))
    _moved_to_another_account(path)

    with pytest.raises(Locked) as locked:
        open_key(path, encrypted=True)

    assert not locked.value.recoverable
    assert recover(path, _sealing()) is Recovery.NONE
    assert recovery_mark(path) is None


@windows
def test_a_sealing_of_another_key_never_replaces_the_one_windows_opens(tmp_path: Path) -> None:
    """A key file edited to seal another key: whichever is right, the one that opens the
    data here is kept (mutation: take the recovered key)."""
    path = tmp_path / "memory.key"
    key = open_key(path, encrypted=False)
    sealing = _sealing()
    stored = json.loads(path.read_text(encoding="utf-8"))
    other = seal(os.urandom(KEY_BYTES), sealing, path.name)
    stored |= {"recovery": base64.b64encode(other).decode("ascii"), "recovery_id": "id-1"}
    path.write_text(json.dumps(stored), encoding="utf-8")
    before = path.read_bytes()

    assert recover(path, sealing) is Recovery.WRONG
    assert path.read_bytes() == before and open_key(path, encrypted=True) == key


@windows
def test_a_key_file_is_rewritten_whole_or_not_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cut short, the key file stays as it was: it is the only way to her data
    (mutation: write it in place)."""
    path = tmp_path / "memory.key"
    key = open_key(path, encrypted=False)
    assert key is not None
    before = path.read_bytes()

    def refused(*_args: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(data_key.os, "replace", refused)
    with pytest.raises(PermissionError):
        seal_recovery({path: key}, _sealing(), "id-1", MADE)

    assert path.read_bytes() == before
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["memory.key"]
