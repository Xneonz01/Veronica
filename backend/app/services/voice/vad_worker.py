"""Voice detection on its own thread.

The detector ran inside the ingest loop, on the event loop: a Silero forward for every
32 ms window and, now, a smart-turn check of about 13 ms at each pause, with the socket,
the outgoing audio and the state machine waiting behind them. One thread per call runs
them instead, in frame order.

One thread and one queue rather than a thread-pool hop per frame: the pool is shared
with recognition and synthesis, and a hop every 20 ms is overhead of its own. The
detector stays synchronous, so the test fakes that implement it need no change.

The thread leaves torch's thread count alone. `torch.set_num_threads(1)` called here
applies to this thread and to every thread started after it (measured: the threads
already running kept 20), which would slow recognition and synthesis on threads the
pool starts later.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from app.services.voice.audio import pcm16_bytes_to_float32
from app.services.voice.turn import TurnEvent

logger = logging.getLogger(__name__)

_JOIN_TIMEOUT_S = 2.0
# About 20 s of 20 ms frames: bounded, so a stuck thread can't grow the heap. A call
# that far behind is already broken, and dropping frames is the lesser harm.
_INBOX_MAX = 1000
# The thread wakes this often to notice `close`, even if the wake-up item was dropped.
_POLL_S = 0.05


class _Detector(Protocol):
    def process_audio(self, samples: Any) -> list[TurnEvent]: ...

    def trailing_silence_ms(self) -> float: ...


@dataclass(frozen=True, slots=True)
class VadResult:
    """One scored frame on its way back to the event loop.

    The end of speech is dated on the thread that scored it: `scored_at` less the
    trailing silence is when the user stopped talking, so the hop back to the loop
    counts as part of the wait they sat through.
    """

    frame: bytes
    events: list[TurnEvent]
    scored_at: float
    trailing_silence_ms: float


class VadWorker:
    """Runs a detector on a thread of its own, one result per frame, in order."""

    def __init__(self, detector: _Detector, clock: Callable[[], float]) -> None:
        self._detector = detector
        self._clock = clock  # the session's, so `scored_at` compares with its readings
        self._inbox: queue.Queue[bytes | None] = queue.Queue(maxsize=_INBOX_MAX)
        self.results: asyncio.Queue[VadResult | None] = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._closed = False
        self.dropped = 0
        self.failed = 0
        self._frames = 0
        self._total_s = 0.0
        self._max_s = 0.0
        self._thread = threading.Thread(target=self._run, name="vad-worker", daemon=True)
        self._thread.start()

    def submit(self, frame: bytes) -> None:
        """Hand one PCM16 frame to the thread; never blocks the event loop."""
        if self._closed:
            return
        try:
            self._inbox.put_nowait(frame)
        except queue.Full:
            self.dropped += 1

    def timing(self) -> dict[str, float]:
        """How long the detector took per frame, in milliseconds; frames lost or failed."""
        mean_ms = self._total_s / self._frames * 1000 if self._frames else 0.0
        return {
            "vad_mean_frame_ms": round(mean_ms, 3),
            "vad_max_frame_ms": round(self._max_s * 1000, 3),
            "vad_dropped": self.dropped,
            "vad_failed": self.failed,
        }

    def close(self) -> None:
        """Stop the thread and wake whoever waits on `results`.

        Blocking (it joins the thread), so `lifecycle.stop` calls it on a thread.
        """
        if self._closed:
            return
        self._closed = True
        # Never a blocking put: on a full inbox it would hang the stop. The wake-up is
        # only a shortcut; the thread's poll notices `_closed` either way.
        with suppress(queue.Full):
            self._inbox.put_nowait(None)
        self._thread.join(timeout=_JOIN_TIMEOUT_S)
        if self._thread.is_alive():
            logger.warning("the voice-detection thread didn't stop within %.1f s", _JOIN_TIMEOUT_S)
        self._post(None)

    def _run(self) -> None:
        while not self._closed:
            try:
                frame = self._inbox.get(timeout=_POLL_S)
            except queue.Empty:
                continue
            if frame is None:
                return
            started = time.perf_counter()
            try:
                events = self._detector.process_audio(pcm16_bytes_to_float32(frame))
            except Exception:
                # The thread must outlive a bad frame: without it the call stays
                # connected and never hears the user again. Said once, then counted,
                # since a detector that fails once fails fifty times a second.
                self.failed += 1
                if self.failed == 1:
                    logger.warning("voice detection failed on a frame", exc_info=True)
                events = []
            elapsed = time.perf_counter() - started
            self._frames += 1
            self._total_s += elapsed
            self._max_s = max(self._max_s, elapsed)
            self._post(
                VadResult(
                    frame=frame,
                    events=events,
                    scored_at=self._clock(),
                    trailing_silence_ms=self._detector.trailing_silence_ms(),
                )
            )

    def _post(self, item: VadResult | None) -> None:
        # `asyncio.Queue` isn't thread-safe: the put runs on the loop.
        try:
            self._loop.call_soon_threadsafe(self.results.put_nowait, item)
        except RuntimeError:
            # The loop closed with a frame in flight; there is no one to deliver to.
            self.dropped += 1
