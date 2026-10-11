"""Turn-taking: Silero for speech against silence, smart-turn for "have they finished".

Silero needs 512-sample windows at 16 kHz, but the transport delivers 20 ms
(320-sample) frames, so the detector buffers and re-chunks. It keeps running during
playback so the session can detect barge-in.

The end of a turn is a window, not a fixed wait. The old rule waited 400 ms of silence
on every turn, which the 32 ms windows made 416 ms every time. Now the `Endpointer` asks
for a decision at `end_silence_min_ms` of silence, asks again every `recheck_ms`, and
ends the turn at `end_silence_max_ms` whatever the answer. Silero still decides speech
against silence; the turn model only decides whether this pause is the end. The
`Endpointer` stays pure: it is handed the answer and never holds the model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
import numpy.typing as npt

from app.adapters.base import TurnArbiter
from app.adapters.turn.smart_turn import WINDOW_SAMPLES
from app.core.config_voice import VadSettings
from app.core.gpu_manager import ModelLoader

logger = logging.getLogger(__name__)

_VAD_WINDOW = 512
# How much audio before the run of voice that started a turn the turn model is given.
# Silero notices a word only once it is loud, so its onset is a little earlier; with none
# of it the model heard clipped questions as unfinished. With the whole wait before the
# turn, it called half-sentences finished. Measured with this detector on the call
# probe's questions (16 finished, 48 cut off partway) at a threshold of 0.5: 0.2 s ended
# 14 finished questions early and 10 half-sentences wrongly (8 at the 0.6 in use); 0 s,
# 1 of 8 finished; everything since the last turn, all 8 half-sentences of 8.
_ONSET_PREROLL_MS = 200


class TurnEvent(StrEnum):
    """Turn-taking events emitted as speech starts and ends."""

    speech_start = "user_speech_start"
    speech_end = "user_speech_end"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One window's outcome: an event, or a request to ask the turn model.

    `arbitrate` asks the caller to score the utterance and hand the answer back through
    `Endpointer.resolve`, so the model runs only on the windows that reach a check, and
    its answer is never a window old.
    """

    event: TurnEvent | None = None
    arbitrate: bool = False


@dataclass
class Endpointer:
    """Converts per-window speech probabilities into start/end turn events."""

    threshold: float
    min_speech_ms: float
    end_silence_min_ms: float
    end_silence_max_ms: float
    recheck_ms: float
    frame_ms: float
    _speaking: bool = field(default=False, init=False)
    _voiced_ms: float = field(default=0.0, init=False)
    _silence_ms: float = field(default=0.0, init=False)
    _next_check_ms: float = field(default=0.0, init=False)

    def process(self, speech_prob: float) -> Endpoint:
        voiced = speech_prob >= self.threshold
        if not self._speaking:
            if not voiced:
                self._voiced_ms = 0.0
                return Endpoint()
            self._voiced_ms += self.frame_ms
            if self._voiced_ms >= self.min_speech_ms:
                self._speaking = True
                self._silence_ms = 0.0
                self._next_check_ms = self.end_silence_min_ms
                return Endpoint(TurnEvent.speech_start)
            return Endpoint()
        if voiced:
            self._silence_ms = 0.0
            self._next_check_ms = self.end_silence_min_ms
            return Endpoint()
        self._silence_ms += self.frame_ms
        if self._silence_ms >= self.end_silence_max_ms:
            # Whatever the model thinks: a call that never takes its turn is broken.
            return Endpoint(self._end())
        if self._silence_ms >= self._next_check_ms:
            self._next_check_ms += self.recheck_ms
            return Endpoint(arbitrate=True)
        return Endpoint()

    def resolve(self, turn_complete: bool) -> TurnEvent | None:
        """Answer an `arbitrate` request; False keeps listening."""
        return self._end() if turn_complete else None

    def _end(self) -> TurnEvent:
        self._speaking = False
        self._voiced_ms = 0.0
        return TurnEvent.speech_end

    def reset(self) -> None:
        self._speaking = False
        self._voiced_ms = 0.0
        self._silence_ms = 0.0
        self._next_check_ms = self.end_silence_min_ms

    @property
    def silence_ms(self) -> float:
        """Silence counted since the last voiced window; kept after `speech_end`."""
        return self._silence_ms

    @property
    def voiced_ms(self) -> float:
        """The run of voice that started the speech; read right after `speech_start`."""
        return self._voiced_ms


def silero_loader() -> ModelLoader:
    """Loader for the Silero VAD model (CPU; not GPU-managed)."""

    def load() -> object:
        from silero_vad import load_silero_vad

        return load_silero_vad()

    return load


class SileroTurnDetector:
    """Buffers audio into 512-sample windows, scores them, and endpoints turns."""

    def __init__(
        self, settings: VadSettings, model: Any, arbiter: TurnArbiter | None = None
    ) -> None:
        self._model = model
        self._sample_rate = settings.sample_rate
        self._turn_threshold = settings.turn_complete_threshold
        # None when smart-turn couldn't be opened: every check then says "not finished"
        # and each turn waits out `end_silence_max_ms`. Slower, but it never cuts the
        # user off.
        self._arbiter = arbiter
        self._buffer = np.zeros(0, dtype=np.float32)
        # What the turn model scores: this turn's audio from just before its speech
        # began (`_ONSET_PREROLL_MS`), at most the last 8 s. Allocated once and shifted
        # only when full.
        self._utterance = np.empty(WINDOW_SAMPLES, dtype=np.float32)
        self._utterance_len = 0
        self._endpointer = Endpointer(
            threshold=settings.threshold,
            min_speech_ms=settings.min_speech_ms,
            end_silence_min_ms=settings.end_silence_min_ms,
            end_silence_max_ms=settings.end_silence_max_ms,
            recheck_ms=settings.recheck_ms,
            frame_ms=_VAD_WINDOW / settings.sample_rate * 1000,
        )
        self._trailing_ms = 0.0

    def process_audio(self, samples: npt.NDArray[np.float32]) -> list[TurnEvent]:
        """Feed float32 audio; return any turn events the window(s) produced."""
        self._buffer = np.concatenate([self._buffer, samples])
        events: list[TurnEvent] = []
        while self._buffer.size >= _VAD_WINDOW:
            window = self._buffer[:_VAD_WINDOW]
            self._buffer = self._buffer[_VAD_WINDOW:]
            self._remember(window)
            decision = self._endpointer.process(self._speech_prob(window))
            event = decision.event
            if event is TurnEvent.speech_start:
                self._keep_from_onset()
            if decision.arbitrate:
                event = self._endpointer.resolve(self._turn_complete())
            if event is TurnEvent.speech_end:
                # Taken here, not later: everything still buffered came after this
                # window, including windows a long frame brings in the same call.
                buffered_ms = self._buffer.size / self._sample_rate * 1000
                self._trailing_ms = self._endpointer.silence_ms + buffered_ms
                # One turn's audio only: the next question is scored on its own.
                self._utterance_len = 0
            if event is not None:
                events.append(event)
        return events

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._utterance_len = 0
        self._endpointer.reset()
        self._trailing_ms = 0.0

    def trailing_silence_ms(self) -> float:
        """How much audio had come in after the last voiced window when speech ended.

        The silence the endpointer counted plus everything fed after the window that
        ended the speech. Read right after `speech_end`, it dates the user's last word.
        """
        return self._trailing_ms

    def _remember(self, window: npt.NDArray[np.float32]) -> None:
        """Add a window to the utterance, keeping its most recent 8 s."""
        overflow = self._utterance_len + window.size - WINDOW_SAMPLES
        if overflow > 0:
            kept = self._utterance_len - overflow
            self._utterance[:kept] = self._utterance[overflow : self._utterance_len]
            self._utterance_len = kept
        self._utterance[self._utterance_len : self._utterance_len + window.size] = window
        self._utterance_len += window.size

    def _keep_from_onset(self) -> None:
        """Drop what came before this turn's speech, but for a moment ahead of its onset."""
        keep_ms = self._endpointer.voiced_ms + _ONSET_PREROLL_MS
        onset = min(self._utterance_len, int(keep_ms / 1000 * self._sample_rate))
        start = self._utterance_len - onset
        self._utterance[:onset] = self._utterance[start : self._utterance_len]
        self._utterance_len = onset

    def _turn_complete(self) -> bool:
        """Ask the turn model whether this pause ends the utterance."""
        if self._arbiter is None:
            return False
        try:
            probability = self._arbiter.probability(self._utterance[: self._utterance_len])
        except Exception:
            # A turn model that fails must not make the call deaf: "not finished" leaves
            # the turn to end at the ceiling, the same answer as having no model.
            logger.warning("the end-of-turn check failed", exc_info=True)
            return False
        return probability >= self._turn_threshold

    def _speech_prob(self, window: npt.NDArray[np.float32]) -> float:
        import torch

        with torch.no_grad():
            return float(self._model(torch.from_numpy(window), self._sample_rate).item())
