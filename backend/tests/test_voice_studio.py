"""Voice Studio service + REST + the app-wide event forwarder."""

from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.api.routes_events import AppEventForwarder
from app.core.event_bus import InProcessEventBus
from app.core.sealed import MAGIC, seal
from app.main import create_app
from app.schemas.events import Event
from app.services.voice.clips import Clips
from app.services.voice.runtime import VoiceRuntime
from app.services.voice.voice_studio import VoiceStudio, VoiceStudioError
from app.services.voice.voices import Voice, VoiceRegistry, VoiceService
from tests.fakes import FakeLLM, FakeManager, FakeSTT


class _FakeTTS:
    """Activates without a real model; synthesizes silence for preview."""

    needs_prompt_text = False

    def __init__(self) -> None:
        self.forgotten: list[str] = []

    async def activate_voice(self, _voice_id: str, *, load: bool = True) -> None:
        return None

    def synthesize_stream(self, _text: str, _voice_id: str) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            yield np.zeros(2400, dtype="<i2").tobytes()  # 0.1 s of 24 kHz PCM16

        return gen()

    async def forget_voice(self, voice_id: str) -> None:
        self.forgotten.append(voice_id)


def _tone_wav(path: Path, seconds: float = 3.0, sr: int = 44100) -> Path:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), sr, subtype="PCM_16")
    return path


def _tone_bytes(seconds: float = 3.0, sr: int = 44100) -> bytes:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    buffer = io.BytesIO()
    sf.write(buffer, (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), sr, format="WAV")
    return buffer.getvalue()


def _seeded_studio(
    tmp_path: Path, bus: InProcessEventBus, clips: Clips | None = None
) -> tuple[VoiceStudio, VoiceService]:
    registry = VoiceRegistry(tmp_path)
    default_wav = _tone_wav(tmp_path / "veronica-default.wav", seconds=2.0)
    registry.add(
        Voice(voice_id="veronica-default", name="Default", reference_wav_path=str(default_wav))
    )
    tts = _FakeTTS()
    service = VoiceService(registry, tts, bus, "veronica-default")
    clips = clips or Clips(os.urandom(32))
    studio = VoiceStudio(registry, tts, service, bus, "veronica-default", clips=clips)
    return studio, service


async def test_create_from_clips_concatenates_registers_activates(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    updated: list[Event] = []

    async def collect(event: Event) -> None:
        updated.append(event)

    bus.subscribe("voices.updated", collect)
    studio, service = _seeded_studio(tmp_path, bus)

    clips = [_tone_bytes(seconds=4.0), _tone_bytes(seconds=4.0)]
    voice = await studio.create_from_clips("My Voice", clips, source="record")

    assert voice.source == "record"
    assert service.active_voice_id == voice.voice_id  # activated
    reference = tmp_path / f"{voice.voice_id}.wav"  # one clean reference, sealed
    assert reference.read_bytes().startswith(MAGIC)
    info = sf.info(io.BytesIO(studio._clips.read(reference)))
    assert info.samplerate == 24_000 and info.duration == pytest.approx(8.0, abs=0.01)
    assert len(updated) == 1  # voices.updated emitted


async def test_create_rejects_too_short(tmp_path: Path) -> None:
    studio, _ = _seeded_studio(tmp_path, InProcessEventBus())
    # 4 s is under the studio's 6 s build floor (the model itself needs more than 5 s).
    with pytest.raises(VoiceStudioError):
        await studio.create_from_clips("Tiny", [_tone_bytes(seconds=4.0)])


async def test_preview_rejects_under_length_reference(tmp_path: Path) -> None:
    # A legacy/short reference surfaces a clean studio error, never the model's raw
    # AssertionError, and never reaches synthesis.
    studio, _ = _seeded_studio(tmp_path, InProcessEventBus())
    short_ref = _tone_wav(tmp_path / "legacy.wav", seconds=3.0)
    studio._registry.add(Voice(voice_id="legacy", name="Legacy", reference_wav_path=str(short_ref)))
    with pytest.raises(VoiceStudioError):
        await studio.preview("legacy")


async def test_valid_clip_builds_registers_and_previews(tmp_path: Path) -> None:
    studio, service = _seeded_studio(tmp_path, InProcessEventBus())
    voice = await studio.create_from_clips("Clear", [_tone_bytes(seconds=12.0)])
    assert service.active_voice_id == voice.voice_id  # registered + activated
    wav = await studio.preview(voice.voice_id)  # a valid-length reference previews cleanly
    assert wav[:4] == b"RIFF" and len(wav) > 44  # a real WAV came back


async def test_delete_active_falls_back_to_default(tmp_path: Path) -> None:
    studio, service = _seeded_studio(tmp_path, InProcessEventBus())
    voice = await studio.create_from_clips("Temp", [_tone_bytes(seconds=7.0)])
    assert service.active_voice_id == voice.voice_id

    fell_back = await studio.delete(voice.voice_id)
    assert fell_back == "veronica-default"
    assert service.active_voice_id == "veronica-default"
    assert not (tmp_path / f"{voice.voice_id}.wav").exists()  # files cleaned up


async def test_deleting_a_voice_forgets_its_conditioning(tmp_path: Path) -> None:
    # A new voice with the same name gets the deleted one's id back.
    studio, _ = _seeded_studio(tmp_path, InProcessEventBus())
    voice = await studio.create_from_clips("Temp", [_tone_bytes(seconds=7.0)])
    tts = studio._tts
    assert isinstance(tts, _FakeTTS)

    await studio.delete(voice.voice_id)
    assert tts.forgotten == [voice.voice_id]


async def test_delete_default_blocked(tmp_path: Path) -> None:
    studio, _ = _seeded_studio(tmp_path, InProcessEventBus())
    with pytest.raises(VoiceStudioError):
        await studio.delete("veronica-default")


async def test_app_event_forwarder_maps_allowlisted_events() -> None:
    sent: list[str] = []

    class _WS:
        async def send_text(self, text: str) -> None:
            sent.append(text)

    forwarder = AppEventForwarder(_WS())  # type: ignore[arg-type]
    await forwarder(Event(type="voice.changed", source="v", payload={"voice_id": "a", "name": "A"}))
    await forwarder(Event(type="voice.metrics", source="v", payload={}))  # not allowlisted
    # Delivery is queued, not inline: publish() must never await a client socket, or a
    # stalled tab back-pressures the live call. Give the drain task a turn.
    for _ in range(100):
        if sent:
            break
        await asyncio.sleep(0.001)

    assert len(sent) == 1
    assert json.loads(sent[0]) == {"type": "voice_changed", "voice_id": "a", "name": "A"}
    await forwarder.aclose()


def _runtime_with_studio(tmp_path: Path, bus: InProcessEventBus) -> VoiceRuntime:
    studio, service = _seeded_studio(tmp_path, bus)
    return VoiceRuntime(
        manager=FakeManager(),  # type: ignore[arg-type]
        stt=FakeSTT([], None),  # type: ignore[arg-type]
        tts=_FakeTTS(),  # type: ignore[arg-type]
        llm=FakeLLM([]),  # type: ignore[arg-type]
        voice_service=service,
        studio=studio,
    )


def test_voices_rest_roundtrip(tmp_path: Path) -> None:
    app = create_app()
    with TestClient(app, base_url="http://localhost") as client:
        app.state.voice_runtime = _runtime_with_studio(tmp_path, app.state.event_bus)

        listing = client.get("/voices")
        assert listing.status_code == 200
        assert listing.json()["active"] == "veronica-default"

        created = client.post(
            "/voices",
            data={"name": "Studio Voice", "consent": "true", "source": "upload"},
            files={"files": ("clip.wav", _tone_bytes(seconds=7.0), "audio/wav")},
        )
        assert created.status_code == 200
        voice_id = created.json()["voice_id"]

        active_now = client.get("/voices").json()
        assert active_now["active"] == voice_id  # auto-activated
        # File locations stay on the server: an install under the user's profile would
        # otherwise put the account name in every response.
        for voice in [created.json(), *active_now["voices"]]:
            assert "reference_wav_path" not in voice and "embedding_path" not in voice

        removed = client.delete(f"/voices/{voice_id}")
        assert removed.status_code == 200
        assert removed.json()["active"] == "veronica-default"  # fell back


def test_create_rejects_without_consent(tmp_path: Path) -> None:
    app = create_app()
    with TestClient(app, base_url="http://localhost") as client:
        app.state.voice_runtime = _runtime_with_studio(tmp_path, app.state.event_bus)
        response = client.post(
            "/voices",
            data={"name": "No Consent", "consent": "false"},
            files={"files": ("clip.wav", _tone_bytes(), "audio/wav")},
        )
    assert response.status_code == 400


def _sealed_voice(studio: VoiceStudio, tmp_path: Path, key: bytes, seconds: float) -> str:
    """Rowan's voice, its clip sealed with `key`."""
    clip = tmp_path / "rowan.wav"
    clip.write_bytes(seal(_tone_bytes(seconds=seconds), key, clip.name))
    studio._registry.add(Voice(voice_id="rowan", name="Rowan", reference_wav_path=str(clip)))
    return "rowan"


async def test_a_sealed_reference_is_measured_opened(tmp_path: Path) -> None:
    """(mutation: measure the sealed file as it is on disk, which reads as nothing)"""
    key = os.urandom(32)
    studio, _ = _seeded_studio(tmp_path, InProcessEventBus(), Clips(key))
    voice_id = _sealed_voice(studio, tmp_path, key, seconds=7.0)

    await studio.ensure_reference_ok(voice_id)  # long enough: no refusal


@pytest.mark.parametrize("damage", ["changed", "put in another's place", "missing"])
async def test_a_clip_that_wont_open_is_said_as_that_not_as_too_short(
    tmp_path: Path, damage: str
) -> None:
    """It read as "too short to clone from" (mutation: measure it as nothing again)."""
    key = os.urandom(32)
    studio, _ = _seeded_studio(tmp_path, InProcessEventBus(), Clips(key))
    voice_id = _sealed_voice(studio, tmp_path, key, seconds=7.0)
    clip = tmp_path / "rowan.wav"
    if damage == "changed":
        sealed = bytearray(clip.read_bytes())
        sealed[-1] ^= 0x01
        clip.write_bytes(bytes(sealed))
    elif damage == "put in another's place":
        clip.write_bytes(seal(_tone_bytes(seconds=7.0), key, "morgan.wav"))
    else:
        clip.rename(tmp_path / "rowan.gone")

    for attempt in (studio.preview(voice_id), studio.ensure_reference_ok(voice_id)):
        with pytest.raises(VoiceStudioError) as refused:
            await attempt
        assert refused.value.code == "reference_unreadable"
        assert "can't be opened" in str(refused.value)
        assert "Delete the voice" in str(refused.value)


async def test_her_default_voice_that_wont_open_is_made_again_not_deleted(
    tmp_path: Path,
) -> None:
    """Found checking 0.34.9 live: told to delete it, which the default voice can't be
    (mutation: the words for any voice)."""
    key = os.urandom(32)
    studio, _ = _seeded_studio(tmp_path, InProcessEventBus(), Clips(key))
    (tmp_path / "veronica-default.wav").write_bytes(seal(_tone_bytes(7.0), key, "other.wav"))

    with pytest.raises(VoiceStudioError) as refused:
        await studio.preview("veronica-default")

    assert refused.value.code == "reference_unreadable"
    assert "scripts.seed_default_voice" in str(refused.value)
    assert "Delete" not in str(refused.value)


async def test_while_her_voices_are_locked_none_is_made_played_or_chosen(tmp_path: Path) -> None:
    """In words that say so, not as a clip too short (mutations: make one without its
    key; measure a locked clip as nothing)."""
    studio, _ = _seeded_studio(tmp_path, InProcessEventBus(), Clips(None, "another_account"))
    voice_id = _sealed_voice(studio, tmp_path, os.urandom(32), seconds=7.0)

    for attempt in (
        studio.create_from_clips("Morgan", [_tone_bytes(seconds=7.0)]),
        studio.preview(voice_id),
        studio.ensure_reference_ok(voice_id),
    ):
        with pytest.raises(VoiceStudioError) as refused:
            await attempt
        assert refused.value.code == "voices_locked"
    assert sorted(path.name for path in tmp_path.glob("*.wav")) == [
        "rowan.wav",
        "veronica-default.wav",
    ]


def test_an_upload_is_made_into_a_voice_without_a_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each clip was written out to one, and a crash before its removal left a plain
    recording of a person's voice behind (mutation: write each to a temporary file)."""

    def written(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a clip was written to a temporary file")

    for name in ("NamedTemporaryFile", "TemporaryFile", "mkstemp"):
        monkeypatch.setattr(tempfile, name, written)
    app = create_app()
    with TestClient(app, base_url="http://localhost") as client:
        app.state.voice_runtime = _runtime_with_studio(tmp_path, app.state.event_bus)
        created = client.post(
            "/voices",
            data={"name": "Studio Voice", "consent": "true"},
            files={"files": ("clip.wav", _tone_bytes(seconds=7.0), "audio/wav")},
        )
    assert created.status_code == 200
