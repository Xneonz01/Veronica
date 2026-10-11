"""The smart-turn adapter: what it hands the model, and what it hands back."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from app.adapters.turn.smart_turn import WINDOW_SAMPLES, SmartTurnArbiter, open_smart_turn
from app.services.voice import runtime as voice_runtime
from tests.fakes import FakeLLM, FakeSTT, FakeTTS, build_fake_runtime


class _Extractor:
    """Records the audio and options it was called with; returns (1, 80, 800) features."""

    def __init__(self) -> None:
        self.calls: list[tuple[np.ndarray, dict[str, Any]]] = []

    def __call__(self, audio: np.ndarray, **options: Any) -> Any:
        self.calls.append((audio, options))
        features = np.zeros((1, 80, 800), dtype=np.float64)
        return type("Batch", (), {"input_features": features})()


class _Session:
    def __init__(self, output: float) -> None:
        self.output = output
        self.fed: list[np.ndarray] = []

    def run(self, _names: Any, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.fed.append(feeds["input_features"])
        return [np.array([[self.output]], dtype=np.float32)]


def test_the_models_output_is_the_probability_as_it_comes() -> None:
    # The output is named "logits" but is already a probability: a second sigmoid would
    # turn 0.02 into 0.5 and end every pause.
    arbiter = SmartTurnArbiter(_Session(0.02), _Extractor())

    assert arbiter.probability(np.zeros(16_000, dtype=np.float32)) == pytest.approx(0.02)


def test_the_model_is_given_its_reference_input() -> None:
    extractor, session = _Extractor(), _Session(0.9)
    arbiter = SmartTurnArbiter(session, extractor)

    arbiter.probability(np.zeros(16_000, dtype=np.float32))

    _audio, options = extractor.calls[0]
    assert options == {
        "sampling_rate": 16_000,
        "return_tensors": "np",
        "padding": "max_length",
        "max_length": WINDOW_SAMPLES,
        "truncation": True,
        "do_normalize": True,
    }
    assert session.fed[0].shape == (1, 80, 800) and session.fed[0].dtype == np.float32


def test_long_audio_is_cut_to_its_last_8_seconds() -> None:
    extractor = _Extractor()
    arbiter = SmartTurnArbiter(_Session(0.9), extractor)
    audio = np.arange(WINDOW_SAMPLES + 500, dtype=np.float32)

    arbiter.probability(audio)

    sent, _options = extractor.calls[0]
    assert sent.size == WINDOW_SAMPLES and sent[0] == 500  # the end kept, the start dropped


@pytest.mark.slow
def test_the_installed_model_opens_and_answers_with_a_probability() -> None:
    """The real file and libraries: that the session builds and the answer is a number
    between 0 and 1 (how well it tells turns apart is measured on real speech; see the
    adapter's docstring)."""
    arbiter = open_smart_turn()

    for audio in (np.zeros(32_000, dtype=np.float32), np.full(32_000, 0.01, dtype=np.float32)):
        assert 0.0 <= arbiter.probability(audio) <= 1.0


async def test_the_runtime_opens_smart_turn_once_and_survives_a_missing_file(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    opened: list[SmartTurnArbiter] = []

    def missing() -> SmartTurnArbiter:
        raise FileNotFoundError("smart-turn-v3.1-cpu.onnx")

    def present() -> SmartTurnArbiter:
        opened.append(SmartTurnArbiter(_Session(0.9), _Extractor()))
        return opened[-1]

    runtime = build_fake_runtime(stt=FakeSTT([], None), tts=FakeTTS(), llm=FakeLLM([]))
    monkeypatch.setattr(voice_runtime, "open_smart_turn", missing)
    assert await runtime.turn_arbiter() is None  # the call still starts
    assert "smart-turn couldn't be opened" in caplog.text

    monkeypatch.setattr(voice_runtime, "open_smart_turn", present)
    first = await runtime.turn_arbiter()  # tried again on the next call
    assert first is not None and await runtime.turn_arbiter() is first
    assert len(opened) == 1  # opened once, shared after
