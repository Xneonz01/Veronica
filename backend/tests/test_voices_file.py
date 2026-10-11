"""The voices file when it isn't what it should be: saved with a mark before the text,
damaged twice, a folder in its place or a file in the folder's, stuck where it is, or
a save cut short. None of it stops her starting, loses a clip, or leaves a voice
listed that isn't saved (0.30.2)."""

from __future__ import annotations

import io
import json
import os
from datetime import UTC, datetime, tzinfo
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.core.event_bus import InProcessEventBus
from app.services.voice import voices as voices_module
from app.services.voice.clips import Clips
from app.services.voice.voice_studio import VoiceStudio, VoiceStudioError
from app.services.voice.voices import Voice, VoiceRegistry, VoiceService

ROWAN = {"voice_id": "rowan", "name": "Rowan", "reference_wav_path": "rowan.wav"}


def _tone(seconds: float = 8.0) -> bytes:
    t = np.linspace(0, seconds, int(24000 * seconds), endpoint=False)
    wav = io.BytesIO()
    sf.write(wav, (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 24000, format="WAV")
    return wav.getvalue()


class _Tts:
    needs_prompt_text = False

    async def activate_voice(self, _voice_id: str, *, load: bool = True) -> None:
        return None

    async def forget_voice(self, _voice_id: str) -> None:
        return None


def _studio(registry: VoiceRegistry) -> VoiceStudio:
    bus = InProcessEventBus()
    service = VoiceService(registry, _Tts(), bus, "veronica-default")
    return VoiceStudio(  # type: ignore[arg-type]
        registry, _Tts(), service, bus, "veronica-default", clips=Clips(os.urandom(32))
    )


def test_a_file_saved_with_a_mark_before_the_text_is_read(tmp_path: Path) -> None:
    """Notepad and Windows PowerShell write one; it was set aside as damaged (mutation:
    read it as plain UTF-8)."""
    (tmp_path / "voices.json").write_bytes(b"\xef\xbb\xbf" + json.dumps([ROWAN]).encode())

    registry = VoiceRegistry(tmp_path)

    assert not registry.damaged and [voice.voice_id for voice in registry.list()] == ["rowan"]


def test_two_damaged_starts_in_one_second_keep_both_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second rename replaced the first copy (mutation: no number on a second)."""

    class _Still(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> _Still:
            return cls(2026, 9, 26, 14, 30, 5, tzinfo=tz)

    monkeypatch.setattr(voices_module, "datetime", _Still)
    for damaged in ("{first", "{second"):
        (tmp_path / "voices.json").write_text(damaged, encoding="utf-8")
        VoiceRegistry(tmp_path)

    kept = sorted(
        path.read_text(encoding="utf-8") for path in tmp_path.glob("voices.json.damaged-*")
    )
    assert kept == ["{first", "{second"]


def test_a_folder_where_the_file_should_be_is_set_aside(tmp_path: Path) -> None:
    """It read as no file at all, and every save then failed (mutation: look for a file
    only)."""
    (tmp_path / "voices.json").mkdir()

    registry = VoiceRegistry(tmp_path)

    assert registry.damaged and not registry.stuck
    assert [path.is_dir() for path in tmp_path.glob("voices.json.damaged-*")] == [True]
    registry.add(Voice(**ROWAN))
    assert (
        json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))[0]["voice_id"] == "rowan"
    )


def test_a_file_where_the_folder_should_be_is_no_reason_not_to_start(tmp_path: Path) -> None:
    """Making the folder stopped the start (mutation: let the error through)."""
    (tmp_path / "voices").write_text("in the way", encoding="utf-8")

    registry = VoiceRegistry(tmp_path / "voices")

    assert registry.damaged and registry.stuck and registry.list() == []
    with pytest.raises(OSError):
        registry.add(Voice(**ROWAN))


def test_a_save_that_fails_lists_nothing_it_did_not_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A voice listed before its save, which then failed, stayed listed until a restart
    lost it (mutation: list first, save after)."""
    registry = VoiceRegistry(tmp_path)

    def refuse(*_args: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(voices_module.os, "replace", refuse)
    with pytest.raises(PermissionError):
        registry.add(Voice(**ROWAN))

    assert registry.list() == [] and not (tmp_path / "voices.json").exists()


def test_a_save_is_whole_or_not_at_all(tmp_path: Path) -> None:
    """Written beside the file and moved over it: cut short, the last save stands
    (mutation: write in place)."""
    registry = VoiceRegistry(tmp_path)
    registry.add(Voice(**ROWAN))
    before = (tmp_path / "voices.json").read_bytes()

    def interrupted(*_args: object, **_kwargs: object) -> int:
        raise OSError(28, "No space left on device")

    target = tmp_path / "voices.json.saving"
    original = Path.write_text

    def half_written(self: Path, *args: object, **kwargs: object) -> int:
        if self == target:
            original(self, "[{", encoding="utf-8")
            interrupted()
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "write_text", half_written)
        with pytest.raises(OSError):
            registry.remove("rowan")

    assert (tmp_path / "voices.json").read_bytes() == before


async def test_a_new_voice_never_writes_over_a_clip_left_in_the_folder(tmp_path: Path) -> None:
    """After a damaged start the list is empty but the clips it named are still here:
    a new voice of the same name replaced one (mutation: judge an id by the list
    alone)."""
    (tmp_path / "rowan.wav").write_bytes(_tone(3.0))
    before = (tmp_path / "rowan.wav").read_bytes()
    (tmp_path / "voices.json").write_text("{damaged", encoding="utf-8")
    registry = VoiceRegistry(tmp_path)

    voice = await _studio(registry).create_from_clips("Rowan", [_tone()])

    assert voice.voice_id != "rowan" and voice.voice_id.startswith("rowan-")
    assert (tmp_path / "rowan.wav").read_bytes() == before


async def test_while_the_file_is_stuck_no_voice_is_made_or_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused in words the app shows, before a clip is written (mutation: make the clip,
    then fail at the save with an internal error)."""
    (tmp_path / "voices.json").write_text("{damaged", encoding="utf-8")

    def refuse(*_args: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(voices_module.os, "replace", refuse)
    registry = VoiceRegistry(tmp_path)
    studio = _studio(registry)
    with pytest.raises(VoiceStudioError) as refused:
        await studio.create_from_clips("Rowan", [_tone()])

    assert refused.value.code == "voices_file_stuck"
    assert list(tmp_path.glob("*.wav")) == []
    assert registry.list() == []


def test_a_voice_keeps_its_created_time_in_utc(tmp_path: Path) -> None:
    """The set-aside name is local time for whoever looks in the folder; a voice's own
    time stays UTC."""
    registry = VoiceRegistry(tmp_path)
    registry.add(Voice(**ROWAN))
    assert registry.get("rowan").created_at.tzinfo == UTC
