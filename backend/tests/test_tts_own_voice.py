"""A voice that isn't there, or whose clip won't open, is spoken in the voice the model
comes with (bug 49), as a locked one is (`test_tts.py`), and a voice made under its id
later is used at once."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.tts import chatterbox_stream
from app.adapters.tts.chatterbox_stream import ChatterboxStreamTTS
from app.core.config import TtsSettings
from app.core.sealed import SealBroken
from app.services.voice.voices import Voice, VoiceRegistry
from tests.test_tts import _clip, _FakeChatterbox, _FakeConds, _manager


async def test_a_voice_that_isnt_there_is_spoken_in_the_models_own_until_it_is_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A data folder without her default voice failed every call's warm-up as "check that
    the backend is running" (mutations: let the missing voice through; keep the model's
    own voice for its id)."""
    model = _FakeChatterbox()
    model.device = "cuda"  # type: ignore[attr-defined]
    loads: list[str] = []

    def builtin(device: str) -> _FakeConds:
        loads.append(device)
        return _FakeConds("built-in")

    monkeypatch.setattr(chatterbox_stream, "builtin_voice", builtin)
    registry = VoiceRegistry(tmp_path)  # no voice at all: the default was never made
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), registry, read_clip=_clip
    )

    await tts.activate_voice("veronica-default")  # the call's warm-up
    _ = [chunk async for chunk in tts.synthesize_stream("Hello there.", "veronica-default")]
    assert [voice for _, voice in model.spoken] == ["built-in"]

    seeded = str(tmp_path / "ref.wav")
    registry.add(Voice(voice_id="veronica-default", name="Veronica", reference_wav_path=seeded))
    _ = [chunk async for chunk in tts.synthesize_stream("And now?", "veronica-default")]

    assert [voice for _, voice in model.spoken] == ["built-in", seeded]
    assert loads == ["cuda"]  # the model's own voice, loaded once


@pytest.mark.parametrize(
    "unusable", [SealBroken("changed since it was sealed"), FileNotFoundError("gone")]
)
async def test_a_voice_whose_clip_wont_open_is_spoken_in_the_models_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unusable: Exception
) -> None:
    """Found checking 0.34.9 live: a clip changed on disk or missing failed the call's
    warm-up as "check that the backend is running" (mutation: let it through)."""
    model = _FakeChatterbox()
    model.device = "cuda"  # type: ignore[attr-defined]
    monkeypatch.setattr(chatterbox_stream, "builtin_voice", lambda _device: _FakeConds("built-in"))
    registry = VoiceRegistry(tmp_path)
    registry.add(Voice(voice_id="rowan", name="Rowan", reference_wav_path=str(tmp_path / "r.wav")))

    def unopened(_path: str) -> bytes:
        raise unusable

    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), registry, read_clip=unopened
    )
    await tts.activate_voice("rowan")  # the call's warm-up
    _ = [chunk async for chunk in tts.synthesize_stream("Hello there.", "rowan")]

    assert [voice for _, voice in model.spoken] == ["built-in"] and model.prepare_calls == []
