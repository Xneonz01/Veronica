"""Turn-taking: the endpointer's window, and the Silero detector with scripted models."""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from app.adapters.base import TurnArbiter
from app.adapters.turn.smart_turn import WINDOW_SAMPLES
from app.core.config import VadSettings
from app.services.voice.turn import Endpoint, Endpointer, SileroTurnDetector, TurnEvent

SILENT, VOICED = 0.1, 0.9


def _endpointer() -> Endpointer:
    # 32 ms windows: speech after 64 ms of voice; asked at 64 ms of silence and every
    # 32 ms after; ended at 128 ms whatever the answer.
    return Endpointer(
        threshold=0.5,
        min_speech_ms=64,
        end_silence_min_ms=64,
        end_silence_max_ms=128,
        recheck_ms=32,
        frame_ms=32,
    )


def _speaking(ep: Endpointer) -> Endpointer:
    ep.process(VOICED)
    assert ep.process(VOICED).event is TurnEvent.speech_start
    return ep


def test_speech_starts_once_it_has_lasted_min_speech() -> None:
    ep = _endpointer()
    assert ep.process(SILENT) == Endpoint()
    assert ep.process(VOICED) == Endpoint()  # 32 ms
    assert ep.process(VOICED) == Endpoint(TurnEvent.speech_start)  # 64 ms


def test_a_burst_shorter_than_min_speech_is_forgotten() -> None:
    ep = _endpointer()
    ep.process(VOICED)
    ep.process(SILENT)  # the partial resets
    assert ep.process(VOICED) == Endpoint()
    assert ep.process(VOICED).event is TurnEvent.speech_start


def test_a_pause_is_put_to_the_turn_model_from_the_floor_until_the_ceiling() -> None:
    ep = _speaking(_endpointer())
    assert ep.process(SILENT) == Endpoint()  # 32 ms: under the floor, nothing asked
    assert ep.process(SILENT) == Endpoint(arbitrate=True)  # 64 ms
    assert ep.resolve(turn_complete=False) is None  # not finished: keep listening
    assert ep.process(SILENT) == Endpoint(arbitrate=True)  # 96 ms: asked again
    assert ep.resolve(turn_complete=False) is None
    assert ep.process(SILENT) == Endpoint(TurnEvent.speech_end)  # 128 ms: the ceiling


def test_a_finished_answer_ends_the_turn_at_the_floor() -> None:
    ep = _speaking(_endpointer())
    ep.process(SILENT)
    assert ep.process(SILENT).arbitrate
    assert ep.resolve(turn_complete=True) is TurnEvent.speech_end
    assert ep.silence_ms == 64  # what dates the last word


def test_speech_during_a_pause_starts_the_count_again() -> None:
    ep = _speaking(_endpointer())
    ep.process(SILENT)
    ep.process(SILENT)  # asked at 64 ms
    ep.resolve(turn_complete=False)
    assert ep.process(VOICED) == Endpoint()  # still talking
    assert ep.process(SILENT) == Endpoint()  # 32 ms into a new pause
    assert ep.process(SILENT).arbitrate  # asked from the floor again, not from 96 ms


def test_a_floor_above_the_ceiling_is_refused() -> None:
    with pytest.raises(ValueError, match="end_silence_min_ms"):
        VadSettings(end_silence_min_ms=900, end_silence_max_ms=800)


# --- the detector ----------------------------------------------------------------------


class _Scalar:
    def __init__(self, value: float) -> None:
        self._value = value

    def item(self) -> float:
        return self._value


class _FakeVad:
    def __init__(self, probs: list[float]) -> None:
        self._probs = probs
        self._i = 0

    def __call__(self, _tensor: object, _sr: int) -> _Scalar:
        prob = self._probs[min(self._i, len(self._probs) - 1)]
        self._i += 1
        return _Scalar(prob)


class _Arbiter(TurnArbiter):
    """Answers from a script, and remembers how much audio it was given each time."""

    def __init__(self, answers: list[float]) -> None:
        self._answers = answers
        self.heard: list[int] = []

    def probability(self, audio: npt.NDArray[np.float32]) -> float:
        self.heard.append(audio.size)
        return self._answers.pop(0)


class _Broken(TurnArbiter):
    def probability(self, audio: npt.NDArray[np.float32]) -> float:
        raise RuntimeError("the model failed")


def _detector(probs: list[float], arbiter: TurnArbiter | None = None) -> SileroTurnDetector:
    settings = VadSettings(
        min_speech_ms=64,
        end_silence_min_ms=64,
        end_silence_max_ms=128,
        recheck_ms=32,
        threshold=0.5,
    )
    return SileroTurnDetector(settings, _FakeVad(probs), arbiter)


def _feed(detector: SileroTurnDetector, windows: int) -> list[Any]:
    events: list[TurnEvent] = []
    for _ in range(windows):  # one VAD window per scripted probability
        events.extend(detector.process_audio(np.zeros(512, dtype=np.float32)))
    return events


def test_the_turn_ends_when_the_model_says_it_is_finished() -> None:
    arbiter = _Arbiter([0.9])
    detector = _detector([SILENT, VOICED, VOICED, SILENT, SILENT], arbiter)

    assert _feed(detector, 5) == [TurnEvent.speech_start, TurnEvent.speech_end]
    assert detector.trailing_silence_ms() == 64


def test_the_model_hears_the_turn_from_just_before_its_onset() -> None:
    # With seconds of silence ahead of a question, the model said "finished" to
    # half-sentences too; with none, it heard clipped questions as unfinished. It
    # gets the run of voice that started the turn, 200 ms before it, and the pause.
    arbiter = _Arbiter([0.9])
    detector = _detector([SILENT] * 20 + [VOICED, VOICED, SILENT, SILENT], arbiter)

    _feed(detector, 24)

    assert arbiter.heard == [4 * 512 + 3200]  # 3,200 samples: 200 ms at 16 kHz


def test_not_finished_waits_for_the_ceiling() -> None:
    arbiter = _Arbiter([0.2, 0.2])
    detector = _detector([VOICED, VOICED, SILENT, SILENT, SILENT, SILENT], arbiter)

    assert _feed(detector, 6) == [TurnEvent.speech_start, TurnEvent.speech_end]
    assert len(arbiter.heard) == 2  # at 64 and 96 ms; the ceiling needed no answer
    assert detector.trailing_silence_ms() == 128


def test_without_a_turn_model_every_turn_waits_for_the_ceiling() -> None:
    detector = _detector([VOICED, VOICED, SILENT, SILENT, SILENT, SILENT])

    assert _feed(detector, 5) == [TurnEvent.speech_start]  # not ended at the floor
    assert _feed(detector, 1) == [TurnEvent.speech_end]


def test_a_turn_model_that_fails_leaves_the_turn_to_the_ceiling() -> None:
    detector = _detector([VOICED, VOICED, SILENT, SILENT, SILENT, SILENT], _Broken())

    assert _feed(detector, 6) == [TurnEvent.speech_start, TurnEvent.speech_end]
    assert detector.trailing_silence_ms() == 128  # the ceiling, not an early end


def test_each_turn_is_scored_on_its_own_audio() -> None:
    arbiter = _Arbiter([0.9, 0.9])
    detector = _detector([VOICED, VOICED, SILENT, SILENT] * 2, arbiter)

    _feed(detector, 8)

    assert arbiter.heard == [4 * 512, 4 * 512]  # not the first turn's audio again


def test_the_turn_model_hears_the_last_8_seconds_at_most() -> None:
    arbiter = _Arbiter([0.9])
    speech = WINDOW_SAMPLES // 512 + 10  # a little over 8 s of it
    detector = _detector([VOICED] * speech + [SILENT, SILENT], arbiter)

    _feed(detector, speech + 2)

    assert arbiter.heard == [WINDOW_SAMPLES]
