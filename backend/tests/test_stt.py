"""Streaming STT: LocalAgreement-2 policy + the adapter (fake Whisper model)."""

from __future__ import annotations

import io
import logging
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from app.adapters.stt import faster_whisper_stream as stt_module
from app.adapters.stt import one_pass
from app.adapters.stt.faster_whisper_stream import STT_MODEL_ID, FasterWhisperStreamSTT
from app.adapters.stt.local_agreement import LocalAgreement, common_prefix
from app.core import gpu_manager as gm
from app.core.config import AsrSettings
from app.core.gpu_manager import CudaGpuModelManager, GpuMode
from app.core.sealed import seal
from app.services.voice.audio import float32_to_pcm16_bytes
from app.services.voice.clips import Clips
from app.services.voice.runtime import clip_transcriber


def test_common_prefix() -> None:
    assert common_prefix(["a", "b", "c"], ["a", "b", "x"]) == ["a", "b"]
    assert common_prefix([], ["a"]) == []
    assert common_prefix(["a"], ["a"]) == ["a"]


def test_local_agreement_confirms_on_second_agreement() -> None:
    policy = LocalAgreement()
    assert policy.add_hypothesis(["hello"]) == []  # first sighting, unconfirmed
    assert policy.add_hypothesis(["hello", "world"]) == ["hello"]
    assert policy.add_hypothesis(["hello", "world", "how"]) == ["world"]
    assert policy.confirmed_text == "hello world"


def test_local_agreement_never_retracts() -> None:
    policy = LocalAgreement()
    policy.add_hypothesis(["hello", "world"])
    policy.add_hypothesis(["hello", "world"])  # confirms "hello world"
    policy.add_hypothesis(["hello"])  # a revision must not retract
    assert policy.confirmed_text == "hello world"


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeWhisper:
    """Walks a partial script (beam 1); returns the full sentence for the final."""

    def __init__(self, partials: list[str], final: str) -> None:
        self._partials = partials
        self._final = final
        self._i = 0

    def transcribe(
        self, audio: object, beam_size: int, **_: object
    ) -> tuple[Iterator[_FakeSegment], None]:
        if beam_size >= 5:
            return iter([_FakeSegment(self._final)]), None
        text = self._partials[min(self._i, len(self._partials) - 1)]
        self._i += 1
        return iter([_FakeSegment(text)]), None


async def _frames(count: int, samples: int = 320) -> AsyncIterator[bytes]:
    chunk = float32_to_pcm16_bytes(np.zeros(samples, dtype=np.float32))
    for _ in range(count):
        yield chunk


async def test_adapter_streams_partials_then_final(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gm, "_cuda_vram_used_gib", lambda: 0.0)
    monkeypatch.setattr(gm, "_free_cuda_cache", lambda: None)
    manager = CudaGpuModelManager(budget_gib=16.0)
    partials = [
        "hello",
        "hello world",
        "hello world how",
        "hello world how are",
        "hello world how are you",
    ]
    fake = _FakeWhisper(partials, "hello world how are you")
    manager.register(STT_MODEL_ID, lambda: fake, {GpuMode.conversation}, 2.0)

    # A configured language keeps the final on `transcribe`, which this fake answers.
    stt = FasterWhisperStreamSTT(manager, AsrSettings(decode_interval_ms=100, language="en"))
    results = [t async for t in stt.stream_transcripts(_frames(60))]

    finals = [t for t in results if t.is_final]
    assert len(finals) == 1
    assert finals[0].text == "hello world how are you"

    texts = [t.text for t in results if not t.is_final]
    assert texts, "expected at least one confirmed partial"
    for earlier, later in zip(texts, texts[1:], strict=False):
        assert later.startswith(earlier)  # monotonic, never rewrites
    assert texts[-1] in ("hello world how are", "hello world how are you")


class _OnePassRecorder:
    """Stands in for `one_pass`: records what the final asks of it."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.encoded_audio: list[int] = []
        self.decoded: list[tuple[object, str, int]] = []

    def encode_once(self, _model: object, audio: np.ndarray) -> tuple[str, str]:
        self.encoded_audio.append(audio.size)
        return "features", "encoding"

    def detect(self, _model: object, encoded: object) -> str:
        assert encoded == "encoding"
        return "en"

    def decode(
        self, _model: object, _features: object, encoded: object, language: str, beam: int
    ) -> str:
        self.decoded.append((encoded, language, beam))
        return self.text


def _manager_with(fake: object, monkeypatch: pytest.MonkeyPatch) -> CudaGpuModelManager:
    monkeypatch.setattr(gm, "_cuda_vram_used_gib", lambda: 0.0)
    monkeypatch.setattr(gm, "_free_cuda_cache", lambda: None)
    manager = CudaGpuModelManager(budget_gib=16.0)
    manager.register(STT_MODEL_ID, lambda: fake, {GpuMode.conversation}, 2.0)
    return manager


async def test_the_final_encodes_the_audio_once_when_the_language_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `transcribe(language=None)` encodes twice: once to detect, once to decode.
    recorder = _OnePassRecorder("how do I boil an egg")
    for name in ("encode_once", "detect", "decode"):
        monkeypatch.setattr(stt_module.one_pass, name, getattr(recorder, name))
    fake = _FakeWhisper(["how"], "never used for the final")
    stt = FasterWhisperStreamSTT(_manager_with(fake, monkeypatch), AsrSettings())
    finals = [t for t in [t async for t in stt.stream_transcripts(_frames(60))] if t.is_final]
    assert [t.text for t in finals] == ["how do I boil an egg"]
    assert recorder.encoded_audio == [60 * 320]  # the whole utterance, encoded once
    assert recorder.decoded == [("encoding", "en", AsrSettings().final_beam_size)]


async def test_a_configured_language_decodes_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    # With the language set there is no detection to share.
    recorder = _OnePassRecorder("unused")
    monkeypatch.setattr(stt_module.one_pass, "encode_once", recorder.encode_once)
    fake = _FakeWhisper(["hello"], "hello there")
    stt = FasterWhisperStreamSTT(_manager_with(fake, monkeypatch), AsrSettings(language="en"))
    finals = [t for t in [t async for t in stt.stream_transcripts(_frames(60))] if t.is_final]
    assert [t.text for t in finals] == ["hello there"]
    assert recorder.encoded_audio == []


class _FakeHfTokenizer:
    """Just enough of `tokenizers.Tokenizer` for faster-whisper's `Tokenizer` to build
    the suppressed-token list: every string maps to one stable id."""

    def token_to_id(self, token: str) -> int:
        return sum(token.encode()) % 50_000 + 1

    def encode(self, text: str, add_special_tokens: bool = False) -> SimpleNamespace:
        return SimpleNamespace(ids=[self.token_to_id(text)])


def test_the_one_pass_options_are_the_ones_transcribe_builds() -> None:
    """`one_pass._options`, field for field, against faster-whisper's own `transcribe`.

    The GPU test compares whole transcripts and needs recorded audio; this runs anywhere.
    `transcribe` runs on a model with nothing loaded and is stopped where it hands its
    options to the decode, so a pin moved to a version that builds them differently fails
    here instead of failing every detected-language final.
    """
    import torch  # noqa: F401  (before faster-whisper, as in the app)
    from faster_whisper import WhisperModel, __version__
    from faster_whisper.feature_extractor import FeatureExtractor

    assert __version__ == "1.2.1"  # one_pass reads its internals; pinned in pyproject
    model = WhisperModel.__new__(WhisperModel)  # no weights: nothing reaches a decode
    model.logger = logging.getLogger("faster_whisper.stub")
    model.hf_tokenizer = _FakeHfTokenizer()
    model.feature_extractor = FeatureExtractor(feature_size=128)
    model.model = SimpleNamespace(is_multilingual=True, n_mels=128)
    model.frames_per_second = 100
    model.detect_language = lambda **_kwargs: ("en", 1.0, [])  # type: ignore[method-assign]
    handed: dict[str, Any] = {}

    def generate_segments(
        _features: Any, tokenizer: Any, options: Any, _progress: bool, _encoded: Any = None
    ) -> Iterator[Any]:
        handed.update(tokenizer=tokenizer, options=options)
        return iter([])

    model.generate_segments = generate_segments  # type: ignore[method-assign]
    beam = AsrSettings().final_beam_size
    model.transcribe(
        np.zeros(16_000, np.float32),
        beam_size=beam,
        language=None,
        condition_on_previous_text=False,
    )

    assert one_pass._options(handed["tokenizer"], beam) == handed["options"]


async def test_a_voice_is_transcribed_from_its_clip_opened_not_its_sealed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For an engine that needs a transcript, the clip's path was transcribed: since
    0.33.0 that is the sealed file, not the sound (mutation: hand over the path again)."""
    key = os.urandom(32)
    sound = b"RIFF-the clip as recorded"
    clip = tmp_path / "rowan.wav"
    clip.write_bytes(seal(sound, key, clip.name))
    heard: list[bytes] = []

    class _Hears(_FakeWhisper):
        def transcribe(self, audio: object, beam_size: int, **_: object) -> Any:
            assert isinstance(audio, io.BytesIO)
            heard.append(audio.getvalue())
            return iter([_FakeSegment(" hello there ")]), None

    stt = FasterWhisperStreamSTT(_manager_with(_Hears([], ""), monkeypatch), AsrSettings())
    transcribe = clip_transcriber(Clips(key).read, stt.transcribe_clip)

    assert await transcribe(str(clip)) == "hello there"
    assert heard == [sound]
