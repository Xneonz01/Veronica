"""Bus emission and queue/task plumbing for the realtime session.

`RealtimeEmitter` is a mixin: its methods only touch attributes `RealtimeSession`
owns, declared below for the type checker. The `State` enum lives here too so the
mixin and the session share it without a circular import.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.event_bus import EventBus
from app.schemas.events import Event
from app.services.voice.realtime.barge_in import BargeInGate
from app.services.voice.realtime.ledger import SpokenLedger
from app.services.voice.realtime.metrics import MetricsSink, Throttle, TurnMetrics
from app.services.voice.realtime.voice_events import (
    CALL_EVENT_TYPES,
    ClientForwarder,
    asr_final_event,
    asr_partial_event,
    assistant_caveat_event,
    assistant_token_event,
    error_event,
    metrics_event,
    state_event,
)
from app.services.voice.runtime import VoiceRuntime
from app.telemetry import current_perf

logger = logging.getLogger(__name__)


class State(StrEnum):
    idle = "idle"
    connecting = "connecting"
    warming_up = "warming_up"
    listening = "listening"
    thinking = "thinking"
    speaking = "speaking"


@dataclass(frozen=True, slots=True)
class ReplyStart:
    """Put on the audio queue ahead of a reply's first audio: the client counts its
    playback position from here, so the mark has to travel with the audio."""

    reply: int


class RealtimeEmitter:
    """Bus emission and small queue/task helpers, mixed into RealtimeSession."""

    # Provided by RealtimeSession.__init__ (declared for the type checker).
    runtime: VoiceRuntime
    audio_in: asyncio.Queue[bytes | None]
    audio_out: asyncio.Queue[bytes | ReplyStart | None]
    barge: BargeInGate
    ledger: SpokenLedger
    reply_id: int
    played_samples: int | None
    reply_entry: dict[str, Any] | None
    reply_caveats: list[dict[str, str]]
    _reply_marked: bool
    _bus: EventBus
    _source: str
    _state: State
    _last_partial: str
    _partial_throttle: Throttle
    _metrics_sink: MetricsSink

    def voice_id(self) -> str:
        return self.runtime.voice_service.active_voice_id

    async def to_thinking(self) -> None:
        await self._set_state(State.thinking)

    async def to_speaking(self) -> None:
        await self._set_state(State.speaking)

    async def to_listening(self) -> None:
        await self._set_state(State.listening)

    async def _set_state(self, new: State) -> None:
        if new == self._state:
            return
        self._state = new
        await self._emit(state_event(new.value, source=self._source))

    async def _emit(self, event: Event) -> None:
        await self._bus.publish(event)

    async def emit_partial(self, text: str) -> None:
        if text == self._last_partial or not self._partial_throttle.ready():
            return
        self._last_partial = text
        await self._emit(asr_partial_event(text, source=self._source))

    async def emit_final(self, text: str) -> None:
        self._last_partial = ""
        await self._emit(asr_final_event(text, source=self._source))

    async def emit_token(self, text: str) -> None:
        await self._emit(assistant_token_event(text, source=self._source))

    async def emit_caveat(self, text: str) -> None:
        await self._emit(assistant_caveat_event(text, source=self._source))

    async def emit_error(self, kind: str, message: str, **detail: str) -> None:
        await self._emit(error_event(kind, message, source=self._source, **detail))

    async def emit_metrics(self, metrics: TurnMetrics) -> None:
        row = metrics.as_dict()
        await self._emit(metrics_event(row, source=self._source))
        try:
            await self._metrics_sink.write(metrics)
        except Exception:
            # A timing row is never worth the call: the file locked by a spreadsheet or
            # a full disk raised through the ingest loop, and the call stopped hearing.
            logger.warning("could not write the turn's metrics row", exc_info=True)
        _log_turn_perf(row)

    def _subscribe_forwarder(self, forwarder: ClientForwarder) -> Callable[[], None]:
        unsubs = [self._bus.subscribe(t, forwarder) for t in CALL_EVENT_TYPES]

        def unsubscribe_all() -> None:
            for unsub in unsubs:
                unsub()

        return unsubscribe_all

    def begin_reply(self) -> None:
        """Start a new reply (a turn's answer, or the greeting) for the client to play:
        its own id, an empty ledger, no position reported yet."""
        self.reply_id += 1
        self._reply_marked = False
        self.ledger.reset()
        self.played_samples = None
        self.reply_entry = None
        self.reply_caveats = []

    def continue_reply(self) -> None:
        """The rest of this reply goes out under a new mark, after a spoken confirmation.

        The user's answer makes the client drop the reply it was playing, and with it
        its position: nothing more of that reply was ever reported, so the gate waited
        on its ceiling and an interruption cut history back to before the question.
        Under its own mark the client counts the rest from zero, and the words heard
        before the question are carried over.
        """
        heard = self.ledger.text_up_to(self.played_samples or 0)
        self.reply_id += 1
        self._reply_marked = False
        self.played_samples = None
        self.ledger.carry(heard.removesuffix("…").strip())

    async def put_audio(self, pcm: bytes) -> None:
        """Queue her voice for the client, as part of the current reply. Every sample
        she sends comes through here, so the ledger's count and the client's agree."""
        if not self._reply_marked:
            self._reply_marked = True
            await self.audio_out.put(ReplyStart(self.reply_id))
        self.barge.playback_started()
        self.ledger.extend(len(pcm) // 2)
        await self.audio_out.put(pcm)

    def _drain_audio_out(self) -> None:
        while True:
            try:
                self.audio_out.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _drain_audio_in(self) -> None:
        while True:
            try:
                self.audio_in.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _reap(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("background task ended with error: %r", exc)


# In the order a turn goes through them (see `metrics.py`). `prefetch_ms` is left
# out: `respond` already logs it as `memory.prefetch`.
_TURN_STAGES = (
    ("voice.endpoint_wait", "endpoint_wait_ms"),
    ("voice.stt_final", "stt_final_ms"),
    ("voice.handoff", "handoff_ms"),
    ("voice.ttft", "ttft_ms"),
    ("voice.chunk_wait", "chunk_wait_ms"),
    ("voice.tts_first_synth", "tts_first_synth_ms"),
    ("voice.first_audio", "first_audio_ms"),
    ("voice.perceived_first_audio", "perceived_first_audio_ms"),
    ("voice.turn_total", "total_ms"),
)


def _log_turn_perf(row: dict[str, Any]) -> None:
    """Copy a finished turn's timings into the performance log.

    A stage with no value (no audio before an interruption) is skipped rather than
    logged as 0, which would drag every percentile down.
    """
    perf = current_perf()
    for stage, key in _TURN_STAGES:
        value = row.get(key)
        if isinstance(value, (int, float)):
            perf.log(
                stage,
                float(value),
                barged_in=bool(row.get("barged_in")),
                reply_capped=bool(row.get("reply_capped")),
                tool_calls=row.get("tool_calls"),
                user_chars=row.get("user_chars"),
                reply_chars=row.get("reply_chars"),
            )
