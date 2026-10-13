"""Her voice clips sealed at rest (`core/sealed.py`, `services/voice/clips.py`): sealed
once at the start and opened into memory, never readable from the disk without the key,
and never written over when the key can't be used. Real DPAPI: these run on Windows."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.core import dpapi
from app.core import sealed as sealed_module
from app.core.data_key import Locked
from app.core.file_lock import held, lock_path
from app.core.sealed import MAGIC, SealBroken, seal, unseal
from app.services.voice import clips as clips_module
from app.services.voice.clips import KEY_NAME, UNUSABLE, Clips

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows service")


def _wav(seconds: float = 1.0) -> bytes:
    buffer = io.BytesIO()
    samples = np.sin(np.linspace(0, 440 * seconds * 2 * np.pi, int(24_000 * seconds)))
    sf.write(buffer, samples.astype(np.float32), 24_000, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def test_a_sealed_file_opens_only_as_itself() -> None:
    """With its key and under its own name: changed, renamed into another's place or
    opened with another key, it is refused (mutations: leave the name out; no tag)."""
    key = os.urandom(32)
    blob = seal(b"RIFF clip", key, "rowan.wav")

    assert unseal(blob, key, "rowan.wav") == b"RIFF clip"
    for name, other_key, changed in (
        ("morgan.wav", key, blob),
        ("rowan.wav", os.urandom(32), blob),
        ("rowan.wav", key, blob[:-1] + bytes([blob[-1] ^ 1])),
    ):
        with pytest.raises(SealBroken):
            unseal(changed, other_key, name)


def test_the_clips_found_plain_are_sealed_and_open_to_the_same_sound(tmp_path: Path) -> None:
    """(mutations: seal nothing; seal and lose the sound)"""
    clip = tmp_path / "rowan.wav"
    orphan = tmp_path / "morgan.wav"  # a set-aside voices.json no longer names it
    clip.write_bytes(_wav())
    orphan.write_bytes(_wav(0.5))
    before = clip.read_bytes()

    clips = Clips.open(tmp_path)

    assert clip.read_bytes().startswith(MAGIC) and orphan.read_bytes().startswith(MAGIC)
    assert b"RIFF" not in clip.read_bytes()
    assert clips.read(clip) == before
    assert sf.info(io.BytesIO(clips.read(clip))).duration == pytest.approx(1.0)
    assert (tmp_path / KEY_NAME).is_file()


def test_a_new_clip_is_written_sealed(tmp_path: Path) -> None:
    clips = Clips.open(tmp_path)
    path = tmp_path / "rowan.wav"

    with path.open("xb") as handle:
        clips.write(handle, path, _wav())

    assert path.read_bytes().startswith(MAGIC) and clips.read(path) == _wav()


def test_sealed_clips_with_a_key_that_cant_be_used_are_locked_and_left_alone(
    tmp_path: Path,
) -> None:
    """Listed, never opened or written, and nothing made over them (mutations: make a
    new key; seal again)."""
    key = os.urandom(32)
    clip = tmp_path / "rowan.wav"
    clip.write_bytes(seal(_wav(), key, clip.name))
    blob = base64.b64encode(dpapi.protect(key, b"another account's")).decode("ascii")
    (tmp_path / KEY_NAME).write_text(json.dumps({"format": 1, "dpapi": blob}), encoding="utf-8")
    before = clip.read_bytes()

    clips = Clips.open(tmp_path)

    assert clips.locked == "another_account"
    with pytest.raises(Locked):
        clips.read(clip)
    with pytest.raises(Locked), (tmp_path / "new.wav").open("xb") as handle:
        clips.write(handle, tmp_path / "new.wav", _wav())
    assert clip.read_bytes() == before


def test_sealed_clips_with_no_key_beside_them_are_locked(tmp_path: Path) -> None:
    clip = tmp_path / "rowan.wav"
    clip.write_bytes(seal(_wav(), os.urandom(32), clip.name))

    clips = Clips.open(tmp_path)

    assert clips.locked == "key_missing" and not (tmp_path / KEY_NAME).exists()


def test_a_sealing_cut_short_is_cleared_and_done_again(tmp_path: Path) -> None:
    clip = tmp_path / "rowan.wav"
    clip.write_bytes(_wav())
    (tmp_path / "rowan.wav.sealing").write_bytes(b"half")

    Clips.open(tmp_path)

    assert not (tmp_path / "rowan.wav.sealing").exists() and clip.read_bytes().startswith(MAGIC)


def test_a_plain_clip_left_by_a_start_that_couldnt_seal_it_still_opens(tmp_path: Path) -> None:
    clips = Clips(None, None)
    clip = tmp_path / "rowan.wav"
    clip.write_bytes(_wav())

    assert clips.read(clip) == _wav()


def test_a_voices_folder_she_cant_use_doesnt_stop_the_start(tmp_path: Path) -> None:
    """A file where the folder should be: no key can be made there, and the start went
    down with the error (mutation: let it through)."""
    folder = tmp_path / "voices"
    folder.write_text("in the way", encoding="utf-8")

    clips = Clips.open(folder)

    assert clips.locked == UNUSABLE
    assert folder.read_text(encoding="utf-8") == "in the way"


def test_a_clip_that_cant_be_read_is_taken_as_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no key beside it, a key made then could lock a sealed clip away for good
    (mutation: take it as plain)."""
    (tmp_path / "rowan.wav").write_bytes(b"held by another program")

    def held(_path: Path) -> bool:
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(clips_module, "is_sealed", held)

    clips = Clips.open(tmp_path)

    assert clips.locked == "key_missing" and not (tmp_path / KEY_NAME).exists()


def test_a_sealing_that_fails_leaves_the_clip_as_it_was_and_nothing_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sealed copy stayed beside the clip until the next start (mutation: leave it)."""
    clip = tmp_path / "rowan.wav"
    clip.write_bytes(_wav())

    def refused(*_args: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(sealed_module.os, "replace", refused)  # shared since 0.35.0

    clips = Clips.open(tmp_path)

    assert clips.locked is None and clip.read_bytes() == _wav()
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "rowan.wav",
        KEY_NAME,
        "voices.lock",
    ]


def test_one_clip_that_cant_be_sealed_leaves_the_others_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: the first clip's error ends the start's sealing)"""
    for name in ("morgan.wav", "rowan.wav"):
        (tmp_path / name).write_bytes(_wav())
    unlink = Path.unlink

    def held(path: Path, missing_ok: bool = False) -> None:
        if path.name == "morgan.wav.sealing":
            raise PermissionError(13, "The process cannot access the file")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held)

    Clips.open(tmp_path)

    assert not (tmp_path / "morgan.wav").read_bytes().startswith(MAGIC)
    assert (tmp_path / "rowan.wav").read_bytes().startswith(MAGIC)


def test_where_there_is_no_dpapi_the_clips_stay_plain_and_new_ones_are_plain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Her voices were unusable at every start there (mutation: ask DPAPI all the same)."""
    monkeypatch.setattr(dpapi, "available", lambda: False)
    clip = tmp_path / "rowan.wav"
    clip.write_bytes(_wav())

    clips = Clips.open(tmp_path)

    assert clips.locked is None and clip.read_bytes() == _wav()
    with (tmp_path / "morgan.wav").open("xb") as handle:
        clips.write(handle, tmp_path / "morgan.wav", _wav(0.5))
    assert (tmp_path / "morgan.wav").read_bytes() == _wav(0.5)
    assert not (tmp_path / KEY_NAME).exists()


def test_a_second_start_waits_for_the_first_to_finish_sealing(tmp_path: Path) -> None:
    """Two starts at once each made a key, and sealed clips with the one then set aside
    (mutation: open the key without the lock)."""
    (tmp_path / "rowan.wav").write_bytes(_wav())
    opened: list[Clips] = []

    with held(lock_path(tmp_path / KEY_NAME)):
        second = threading.Thread(target=lambda: opened.append(Clips.open(tmp_path)))
        second.start()
        second.join(0.3)
        assert second.is_alive() and not (tmp_path / KEY_NAME).exists()
    second.join(10)

    assert opened and opened[0].locked is None
    assert (tmp_path / "rowan.wav").read_bytes().startswith(MAGIC)
