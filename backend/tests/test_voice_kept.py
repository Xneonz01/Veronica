"""The voice she was set to speak in, kept across restarts (bug 47): choosing one changed
only the running backend, so every start went back to the default."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.voice import active_voice
from app.services.voice.active_voice import FILE_NAME, KeptVoice, starting_voice
from app.services.voice.runtime import build_voice_runtime
from app.services.voice.voices import Voice, VoiceRegistry, VoiceService

DEFAULT = "veronica-default"
ROWAN = Voice(voice_id="rowan", name="Rowan", reference_wav_path="rowan.wav")


class _Tts:
    needs_prompt_text = False

    async def activate_voice(self, _voice_id: str, *, load: bool = True) -> None:
        return None


def _registry(folder: Path) -> VoiceRegistry:
    """Her voices with Rowan's, clip and all: one without its clip isn't started in."""
    registry = VoiceRegistry(folder)
    registry.add(ROWAN)
    (folder / "rowan.wav").write_bytes(b"RIFF")
    return registry


async def test_the_voice_chosen_is_the_one_she_starts_in(tmp_path: Path) -> None:
    """Kept in the voices folder, so it moves with her data (mutation: keep nothing)."""
    kept = KeptVoice(tmp_path)
    service = VoiceService(_registry(tmp_path), _Tts(), InProcessEventBus(), DEFAULT, kept=kept)  # type: ignore[arg-type]

    await service.set_active("rowan")

    restarted = VoiceRegistry(tmp_path)
    assert starting_voice(KeptVoice(tmp_path).read(), restarted, DEFAULT) == "rowan"


async def test_a_start_speaks_in_the_kept_voice(tmp_path: Path) -> None:
    """(mutation: start in the setting's voice whatever was kept)"""
    settings = get_settings()
    voice = settings.voice.model_copy(update={"voices_dir": str(tmp_path)})
    _registry(tmp_path)
    KeptVoice(tmp_path).write("rowan")

    runtime = await build_voice_runtime(
        settings.model_copy(update={"voice": voice}), InProcessEventBus()
    )

    assert runtime.voice_service.active_voice_id == "rowan"
    await runtime.llm.aclose()


def test_a_kept_voice_since_removed_gives_way_to_the_default(tmp_path: Path) -> None:
    """(mutation: start in whatever was kept)"""
    registry = _registry(tmp_path)
    KeptVoice(tmp_path).write("morgan")  # a voice removed since

    assert starting_voice(KeptVoice(tmp_path).read(), registry, DEFAULT) == DEFAULT
    assert starting_voice(None, registry, DEFAULT) == DEFAULT


@pytest.mark.parametrize("text", ["{not json", "[]", '{"voice_id": ""}', '{"voice_id": 7}'])
def test_a_kept_choice_that_cant_be_read_is_no_choice(tmp_path: Path, text: str) -> None:
    (tmp_path / FILE_NAME).write_text(text, encoding="utf-8")

    assert KeptVoice(tmp_path).read() is None


def test_a_choice_that_cant_be_kept_is_recorded_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The change still applies until the backend stops (mutation: let it raise)."""
    events: list[str] = []

    class _Box:
        def record(self, _level: str, event: str, **_fields: object) -> None:
            events.append(event)

    monkeypatch.setattr(active_voice, "current_blackbox", lambda: _Box())
    (tmp_path / FILE_NAME).mkdir()  # a folder where the file should be

    KeptVoice(tmp_path).write("rowan")

    assert events == ["voice.choice_unkept"]


def test_a_kept_voice_whose_clip_is_gone_gives_way_to_the_default(tmp_path: Path) -> None:
    """Every call's warm-up failed on it, and a restart no longer mended that (mutation:
    judge by the list alone)."""
    registry = _registry(tmp_path)
    (tmp_path / "rowan.wav").unlink()

    assert starting_voice("rowan", registry, DEFAULT) == DEFAULT


async def test_two_switches_at_once_keep_the_last(tmp_path: Path) -> None:
    """Written over each other from two threads, the kept file ended unreadable, or on
    the voice that lost (mutation: no lock)."""
    registry = _registry(tmp_path)
    registry.add(Voice(voice_id="morgan-" + "x" * 40, name="Morgan", reference_wav_path="m.wav"))
    (tmp_path / "m.wav").write_bytes(b"RIFF")
    service = VoiceService(registry, _Tts(), InProcessEventBus(), DEFAULT, kept=KeptVoice(tmp_path))  # type: ignore[arg-type]

    for _ in range(40):
        await asyncio.gather(service.set_active("rowan"), service.set_active("morgan-" + "x" * 40))
        assert KeptVoice(tmp_path).read() == service.active_voice_id


async def test_a_choice_that_cant_be_kept_still_applies_and_is_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: a failure to keep it stops the switch)"""
    monkeypatch.setattr(active_voice, "current_blackbox", lambda: _Quiet())
    (tmp_path / FILE_NAME).mkdir()  # a folder where the file should be
    bus = InProcessEventBus()
    heard: list[Event] = []

    async def hear(event: Event) -> None:
        heard.append(event)

    bus.subscribe("voice.changed", hear)
    service = VoiceService(_registry(tmp_path), _Tts(), bus, DEFAULT, kept=KeptVoice(tmp_path))  # type: ignore[arg-type]

    await service.set_active("rowan")

    assert service.active_voice_id == "rowan"
    assert [event.payload["voice_id"] for event in heard] == ["rowan"]


class _Quiet:
    def record(self, *_args: object, **_fields: object) -> None:
        return None


def test_files_nested_too_deep_to_parse_are_no_reason_not_to_start(tmp_path: Path) -> None:
    """json raised RecursionError, which nothing caught, and the start failed
    (mutation: catch only what it caught before)."""
    (tmp_path / FILE_NAME).write_text("[" * 200_000, encoding="utf-8")
    (tmp_path / "voices.json").write_text("[" * 200_000, encoding="utf-8")

    assert KeptVoice(tmp_path).read() is None
    registry = VoiceRegistry(tmp_path)
    assert registry.damaged and registry.list() == []
