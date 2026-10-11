"""Per-turn latency metrics, a JSONL sink, and a small emission throttle.

Durations use `time.perf_counter`. `time.monotonic` only advances in steps of about
15.6 ms on Windows, which would round every latency to a step. The wall clock is
used only for the serialized `ts`.

A turn is timed in stages, from the user's last word to the first reply audio:

    last word -endpoint_wait-> end of speech decided -stt_final-> transcript
    -handoff-> respond() -ttft-> first token -chunk_wait-> first sentence to TTS
    -tts_first_synth-> first audio

`perceived_first_audio` spans the whole chain and equals the sum of its stages, to
within rounding. The last word is dated from the silence the endpointer counted
after it, which assumes the audio arrived in real time, as a microphone's does.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.paths import anchored

_Clock = Callable[[], float]


@dataclass
class UtteranceMarks:
    """When the user stopped speaking, and when their words were transcribed.

    These happen before `respond` starts, so they travel with the utterance and are
    handed to that turn's `TurnMetrics`. Readings of the session clock.
    """

    last_voiced: float | None = None
    endpoint: float | None = None
    stt_final: float | None = None

    def mark_endpoint(self, now: float, last_voiced: float) -> None:
        """Record the end-of-speech decision, and when the last word before it was."""
        self.endpoint = now
        self.last_voiced = last_voiced


@dataclass
class TurnMetrics:
    """Clock readings for one reply turn; `as_dict` gives the stages in whole ms."""

    user_chars: int
    clock: _Clock = field(default=time.perf_counter, repr=False)
    utterance: UtteranceMarks = field(default_factory=UtteranceMarks)
    t0: float = field(init=False, default=0.0)
    reply_chars: int = field(init=False, default=0)
    barged_in: bool = field(init=False, default=False)
    # The reply reached its ceiling and was ended there (`reply_cap.py`).
    reply_capped: bool = field(init=False, default=False)
    # A tool turn's first token waits for the tool to run, so its timings are not
    # comparable with a plain reply's; the count lets a reader tell them apart.
    tool_calls: int = field(init=False, default=0)
    prefetch_s: float | None = field(init=False, default=None)
    _first_token: float | None = field(init=False, default=None)
    _first_chunk: float | None = field(init=False, default=None)
    _first_audio: float | None = field(init=False, default=None)
    _done: float | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.t0 = self.clock()

    def mark_ttft(self) -> None:
        if self._first_token is None:
            self._first_token = self.clock()

    def mark_first_chunk(self) -> None:
        if self._first_chunk is None:
            self._first_chunk = self.clock()

    def mark_first_audio(self) -> None:
        if self._first_audio is None:
            self._first_audio = self.clock()

    def mark_done(self, reply_chars: int) -> None:
        self._done = self.clock()
        self.reply_chars = reply_chars

    def as_dict(self) -> dict[str, Any]:
        heard = self.utterance
        return {
            "ts": datetime.now(UTC).isoformat(),
            "user_chars": self.user_chars,
            "reply_chars": self.reply_chars,
            "tool_calls": self.tool_calls,
            "endpoint_wait_ms": _ms(heard.last_voiced, heard.endpoint),
            "stt_final_ms": _ms(heard.endpoint, heard.stt_final),
            "handoff_ms": _ms(heard.stt_final, self.t0),
            "prefetch_ms": None if self.prefetch_s is None else round(self.prefetch_s * 1000),
            "ttft_ms": _ms(self.t0, self._first_token),
            "chunk_wait_ms": self._chunk_wait_ms(),
            "tts_first_synth_ms": _ms(self._first_chunk, self._first_audio),
            "first_audio_ms": _ms(self.t0, self._first_audio),
            "perceived_first_audio_ms": _ms(heard.last_voiced, self._first_audio),
            "total_ms": _ms(self.t0, self._done),
            "barged_in": self.barged_in,
            "reply_capped": self.reply_capped,
        }

    def _chunk_wait_ms(self) -> int | None:
        # When a slow tool runs first, the first thing said is the spoken
        # acknowledgement, before the model has written a word: there is no wait on
        # the model's text to report.
        if self._first_token is None or self._first_chunk is None:
            return None
        if self._first_chunk < self._first_token:
            return None
        return _ms(self._first_token, self._first_chunk)


def _ms(start: float | None, end: float | None) -> int | None:
    """Whole milliseconds from one reading to another; None if either is missing."""
    if start is None or end is None:
        return None
    return round((end - start) * 1000)


class MetricsSink:
    """Appends turn metrics as JSONL, off the event loop.

    A relative path is anchored to the repository root, like the other data paths,
    so the file doesn't move with the directory the server was started from.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = anchored(path)

    async def write(self, metrics: TurnMetrics) -> None:
        await asyncio.to_thread(self._append, metrics.as_dict())

    def _append(self, row: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")


class Throttle:
    """Allow an action at most once per interval (for asr_partial emission)."""

    def __init__(self, min_interval_s: float, clock: _Clock = time.perf_counter) -> None:
        self._interval = min_interval_s
        self._clock = clock
        self._last = float("-inf")

    def ready(self) -> bool:
        now = self._clock()
        if now - self._last >= self._interval:
            self._last = now
            return True
        return False
