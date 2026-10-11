"""TTS: text utils, Chatterbox adapter (fake model), voice registry + service."""

from __future__ import annotations

import ast
import asyncio
import inspect
import io
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from app.adapters.tts import chatterbox_stream
from app.adapters.tts.chatterbox_stream import TTS_MODEL_ID, ChatterboxStreamTTS
from app.adapters.tts.interruptible import install_abandon_check
from app.adapters.tts.text import sanitize_for_tts, split_sentences
from app.core import gpu_manager as gm
from app.core.config import TtsSettings
from app.core.data_key import Locked
from app.core.event_bus import InProcessEventBus
from app.core.gpu_manager import CudaGpuModelManager, GpuMode
from app.schemas.events import Event
from app.services.voice.voices import Voice, VoiceRegistry, VoiceService


def test_sanitize_strips_markdown_and_emoji() -> None:
    assert sanitize_for_tts("**Hello** _world_ `code`") == "Hello world code"
    assert sanitize_for_tts("See [docs](http://x) now") == "See docs now"
    assert sanitize_for_tts("Hi 🚀🔥 there") == "Hi there"


def test_split_sentences() -> None:
    assert split_sentences("Hello world. How are you? Good!") == [
        "Hello world.",
        "How are you?",
        "Good!",
    ]
    assert split_sentences("   ") == []


def test_split_sentences_keeps_an_abbreviation_with_its_sentence() -> None:
    # Split there, "Ask Dr." was spoken as an utterance of its own.
    assert split_sentences("Ask Dr. Smith. The exam is at 10 a.m. on Sunday. Thanks!") == [
        "Ask Dr. Smith.",
        "The exam is at 10 a.m. on Sunday.",
        "Thanks!",
    ]
    assert split_sentences("It was Mr.") == ["It was Mr."]  # a last one stays whole


class _FakeConds:
    def __init__(self, ref: str) -> None:
        self.ref = ref


def _clip(path: str) -> bytes:
    """A voice's clip, opened. The fake model takes its bytes for the voice they clone,
    as the real one does, and these bytes are the clip's path."""
    return path.encode()


class _FakeChatterbox:
    def __init__(self) -> None:
        self.conds: _FakeConds | None = None
        self.prepare_calls: list[str] = []
        self.generate_kwargs: dict[str, object] = {}
        self.spoken: list[tuple[str, str | None]] = []  # each sentence, with its voice

    def prepare_conditionals(self, clip: io.BytesIO) -> None:
        voice = clip.read().decode()
        self.prepare_calls.append(voice)
        self.conds = _FakeConds(voice)

    def generate(self, text: str, **kwargs: object) -> torch.Tensor:
        self.generate_kwargs = kwargs
        self.spoken.append((text, self.conds.ref if self.conds else None))
        return torch.zeros(1, max(1, len(text)) * 8, dtype=torch.float32)


class _SlowTurbo:
    """A `generate` of 10 ms tokens, each embedded through `t3.speech_emb` as Turbo's are."""

    def __init__(self, tokens: dict[str, int]) -> None:
        self._embedding = torch.nn.Embedding(4, 2)
        self.t3 = SimpleNamespace(speech_emb=self._embedding)
        self.conds: object | None = None
        self._tokens = tokens
        self.order: list[str] = []
        self.taken: dict[str, int] = {}
        self.started = threading.Event()

    def generate(self, text: str, **_kwargs: object) -> torch.Tensor:
        self.order.append(f"in:{text}")
        # Before `started`: a stop that lands before the first token has taken none, and
        # the count was missing then (the test failed on a busy machine, a race of its own).
        self.taken[text] = 0
        self.started.set()
        try:
            for token in range(self._tokens[text]):
                self._embedding(torch.zeros(1, dtype=torch.long))
                self.taken[text] = token + 1
                time.sleep(0.01)
        finally:
            self.order.append(f"out:{text}")
        return torch.zeros(1, 240)


def _manager(monkeypatch: pytest.MonkeyPatch, model: _FakeChatterbox) -> CudaGpuModelManager:
    monkeypatch.setattr(gm, "_cuda_vram_used_gib", lambda: 0.0)
    monkeypatch.setattr(gm, "_free_cuda_cache", lambda: None)
    manager = CudaGpuModelManager(budget_gib=16.0)
    manager.register(TTS_MODEL_ID, lambda: model, {GpuMode.conversation}, 3.0)
    return manager


def _registry(tmp_path: Path) -> VoiceRegistry:
    registry = VoiceRegistry(tmp_path)
    registry.add(
        Voice(
            voice_id="veronica-default",
            name="Veronica",
            reference_wav_path=str(tmp_path / "ref.wav"),
        )
    )
    registry.add(Voice(voice_id="alt", name="Alt", reference_wav_path=str(tmp_path / "alt.wav")))
    return registry


async def test_synthesize_streams_pcm_per_sentence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _FakeChatterbox()
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )

    chunks = [
        c async for c in tts.synthesize_stream("**Hello** world. How are you?", "veronica-default")
    ]
    assert len(chunks) == 2  # one chunk per sentence
    assert all(isinstance(c, bytes) and len(c) > 0 for c in chunks)
    assert model.prepare_calls == [str(tmp_path / "ref.wav")]  # conditioning prepared once


async def test_voice_switch_reuses_cached_conditioning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _FakeChatterbox()
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )

    await tts.activate_voice("veronica-default")
    await tts.activate_voice("alt")
    await tts.activate_voice("veronica-default")  # cached -> no re-prepare

    assert model.prepare_calls == [str(tmp_path / "ref.wav"), str(tmp_path / "alt.wav")]
    assert model.conds is not None
    assert model.conds.ref == str(tmp_path / "ref.wav")  # swapped back to default


async def test_two_voices_speaking_at_once_each_keep_their_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A preview during a call switched the rest of the call's reply to its voice.

    The voice was set once per reply, and each sentence after it is an `await`, so a
    second reply in another voice re-pointed the model between two sentences.
    """
    model = _FakeChatterbox()
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )

    async def speak(voice_id: str, text: str) -> None:
        async for _ in tts.synthesize_stream(text, voice_id):
            await asyncio.sleep(0)  # the audio queue yields between sentences

    await asyncio.gather(
        speak("veronica-default", "One for you. Two for you. Three for you."),
        speak("alt", "One for me. Two for me. Three for me."),
    )

    assert len(model.spoken) == 6
    for text, voice in model.spoken:
        expected = tmp_path / ("ref.wav" if text.endswith("you.") else "alt.wav")
        assert voice == str(expected), f"{text!r} was said in the other voice"


async def test_an_interrupted_sentence_is_out_of_the_model_before_the_next_goes_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A barge-in cancels the task waiting on `generate`, and a thread can't be cancelled.

    The thread was left running: the next sentence went into the model beside it, and
    the abandoned one ran to its end on the GPU. It stops at its next token now, and the
    next sentence goes in once it is out.
    """
    first = "This first sentence is interrupted while it is spoken."
    second = "And this one is what she says instead."
    model = _SlowTurbo({first: 200, second: 3})
    install_abandon_check(model)
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )
    tts._conds_cache["veronica-default"] = object()

    async def say(text: str) -> None:
        async for _ in tts.synthesize_stream(text, "veronica-default"):
            pass

    speaking = asyncio.create_task(say(first))
    assert await asyncio.to_thread(model.started.wait, 5.0)
    speaking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await speaking
    await say(second)

    assert model.order == [f"in:{first}", f"out:{first}", f"in:{second}", f"out:{second}"]
    assert model.taken[first] < 200, "the abandoned sentence ran to its end"
    assert model.taken[second] == 3  # and the stop didn't outlive the sentence it stopped


def test_the_voice_lock_is_never_held_across_a_yield() -> None:
    """A caller that stops reading must not keep the lock until the generator is finalised.

    Read from the source, because a test that walks away can't tell the two apart:
    `aclose()` releases the lock wherever the `yield` is.
    """
    source = textwrap.dedent(inspect.getsource(ChatterboxStreamTTS.synthesize_stream))
    held = [
        node
        for block in ast.walk(ast.parse(source))
        if isinstance(block, ast.AsyncWith)
        for node in ast.walk(block)
        if isinstance(node, ast.Yield)
    ]
    assert held == []


async def test_a_forgotten_voice_is_prepared_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A new voice given a deleted voice's id is cloned from its own clip.
    model = _FakeChatterbox()
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )

    await tts.activate_voice("alt")
    await tts.forget_voice("alt")
    await tts.activate_voice("alt")

    assert model.prepare_calls == [str(tmp_path / "alt.wav")] * 2


class _SlowPrepare(_FakeChatterbox):
    """A preparation that takes a moment on its thread, as a real one takes a second."""

    def __init__(self) -> None:
        super().__init__()
        self.preparing = threading.Event()

    def prepare_conditionals(self, clip: io.BytesIO) -> None:
        self.preparing.set()
        time.sleep(0.2)
        super().prepare_conditionals(clip)


async def test_a_preparation_cut_short_leaves_no_voice_mixed_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hanging up while a new voice is prepared still ends with the model holding it.

    The thread finishes, and the adapter remembered the voice it had set before, so the
    next sentence in that voice came out in the other one.
    """
    model = _SlowPrepare()
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )
    await tts.activate_voice("veronica-default")
    warming = asyncio.create_task(tts.activate_voice("alt"))
    assert await asyncio.to_thread(model.preparing.wait, 5.0)
    warming.cancel()
    with pytest.raises(asyncio.CancelledError):
        await warming

    _ = [chunk async for chunk in tts.synthesize_stream("Hello there.", "veronica-default")]
    assert model.spoken == [("Hello there.", str(tmp_path / "ref.wav"))]


async def test_a_model_loaded_again_is_given_its_voice_again(tmp_path: Path) -> None:
    # A model loaded again comes back with Chatterbox's own voice, which the adapter
    # took for the voice it had set on the model before.
    first, second = _FakeChatterbox(), _FakeChatterbox()
    loaded = [first]

    class _Reloading:
        async def get(self, _model_id: str) -> _FakeChatterbox:
            return loaded[-1]

    tts = ChatterboxStreamTTS(_Reloading(), TtsSettings(), _registry(tmp_path), read_clip=_clip)  # type: ignore[arg-type]
    await tts.activate_voice("veronica-default")
    second.conds = _FakeConds("built-in")
    loaded.append(second)

    _ = [chunk async for chunk in tts.synthesize_stream("Hello there.", "veronica-default")]
    assert second.spoken == [("Hello there.", str(tmp_path / "ref.wav"))]


async def test_a_voice_deleted_while_it_is_prepared_stays_forgotten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The preview's preparation finished after the delete and cached the voice again,
    # so a new voice of the same name would have spoken with the deleted clone.
    model = _SlowPrepare()
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )
    previewing = asyncio.create_task(tts.activate_voice("alt"))
    assert await asyncio.to_thread(model.preparing.wait, 5.0)

    await tts.forget_voice("alt")
    await previewing
    assert "alt" not in tts._conds_cache


async def test_generate_receives_configured_turbo_params(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _FakeChatterbox()
    settings = TtsSettings(temperature=0.6)  # an override must flow through to generate
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), settings, _registry(tmp_path), read_clip=_clip
    )

    _ = [c async for c in tts.synthesize_stream("Hello there.", "veronica-default")]

    assert model.generate_kwargs == {
        "temperature": 0.6,
        "repetition_penalty": 1.2,
        "top_p": 0.95,
        "top_k": 1000,
    }
    # Turbo ignores these params, so they are never passed (no misleading dead config)
    assert not {"cfg_weight", "exaggeration", "min_p"} & set(model.generate_kwargs)


async def test_empty_text_yields_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    model = _FakeChatterbox()
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )
    assert [c async for c in tts.synthesize_stream("   ", "veronica-default")] == []


def test_registry_persists_round_trip(tmp_path: Path) -> None:
    registry = VoiceRegistry(tmp_path)
    registry.add(Voice(voice_id="v1", name="One", reference_wav_path="a.wav", prompt_text="hi"))

    reloaded = VoiceRegistry(tmp_path)
    assert reloaded.has("v1")
    assert reloaded.get("v1").prompt_text == "hi"
    assert [v.voice_id for v in reloaded.list()] == ["v1"]

    reloaded.remove("v1")
    assert not VoiceRegistry(tmp_path).has("v1")


async def test_set_active_emits_voice_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _FakeChatterbox()
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=_clip
    )
    bus = InProcessEventBus()
    events: list[Event] = []

    async def handler(event: Event) -> None:
        events.append(event)

    bus.subscribe("voice.changed", handler)
    service = VoiceService(_registry(tmp_path), tts, bus, active_voice_id="veronica-default")
    await service.set_active("alt")

    assert service.active_voice_id == "alt"
    assert len(events) == 1
    assert events[0].payload["voice_id"] == "alt"


async def test_register_from_clip_transcribes_when_engine_needs_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _FakeChatterbox()
    registry = _registry(tmp_path)
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), registry, read_clip=_clip
    )

    async def fake_transcriber(_path: str) -> str:
        return "this is the cloned voice sample"

    service = VoiceService(
        registry,
        tts,
        InProcessEventBus(),
        "veronica-default",
        transcriber=fake_transcriber,
        needs_prompt_text=True,
    )
    voice = await service.register_from_clip("custom", "Custom", str(tmp_path / "custom.wav"))

    assert voice.prompt_text == "this is the cloned voice sample"
    assert registry.has("custom")
    assert service.active_voice_id == "custom"


async def test_register_from_clip_skips_transcription_when_not_needed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _FakeChatterbox()
    registry = _registry(tmp_path)
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), registry, read_clip=_clip
    )
    called = False

    async def fake_transcriber(_path: str) -> str:
        nonlocal called
        called = True
        return "unused"

    # Chatterbox-style: needs_prompt_text defaults False, so the clip is not transcribed.
    service = VoiceService(
        registry, tts, InProcessEventBus(), "veronica-default", transcriber=fake_transcriber
    )
    voice = await service.register_from_clip("custom", "Custom", str(tmp_path / "custom.wav"))

    assert called is False
    assert voice.prompt_text == ""
    assert service.active_voice_id == "custom"


async def test_choosing_a_voice_never_loads_the_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # After a call the voice model is released; choosing a voice then loaded it again,
    # and it stayed loaded until the next call ended. The next load applies the choice.
    model = _FakeChatterbox()
    manager = _manager(monkeypatch, model)
    tts = ChatterboxStreamTTS(manager, TtsSettings(), _registry(tmp_path), read_clip=_clip)
    service = VoiceService(
        _registry(tmp_path), tts, InProcessEventBus(), active_voice_id="veronica-default"
    )

    await service.set_active("alt")  # the Voice Studio's choice, and her own tool's

    assert manager.is_loaded(TTS_MODEL_ID) is False
    assert model.prepare_calls == []
    chunks = [c async for c in tts.synthesize_stream("Hello there.", "alt")]
    assert chunks and model.conds is not None
    assert model.conds.ref == str(tmp_path / "alt.wav")  # the chosen voice, at the load


async def test_a_locked_voice_is_spoken_in_the_models_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Her voices' key can't be used here: a call still speaks, in the voice the model
    comes with, loaded once for every locked voice (mutations: let the lock through;
    load it for each voice)."""
    model = _FakeChatterbox()
    loads: list[str] = []

    def builtin(device: str) -> _FakeConds:
        loads.append(device)
        return _FakeConds("built-in")

    def locked(_path: str) -> bytes:
        raise Locked("another_account")

    monkeypatch.setattr(chatterbox_stream, "builtin_voice", builtin)
    model.device = "cuda"  # type: ignore[attr-defined]
    tts = ChatterboxStreamTTS(
        _manager(monkeypatch, model), TtsSettings(), _registry(tmp_path), read_clip=locked
    )

    for voice in ("veronica-default", "alt", "veronica-default"):
        _ = [chunk async for chunk in tts.synthesize_stream("Hello there.", voice)]

    assert [voice for _, voice in model.spoken] == ["built-in"] * 3
    assert loads == ["cuda"] and model.prepare_calls == []
