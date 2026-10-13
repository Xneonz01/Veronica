"""RealtimeSession: one hands-free voice call over one WebSocket.

States: idle -> connecting -> warming_up -> listening -> thinking -> speaking ->
listening. Voice detection runs on its own thread (`VadWorker`); the ingest loop
takes its results and never blocks on work; the STT task spawns the reply;
barge-in cancels the reply and flushes queued audio.
Call start/stop live in `lifecycle.py`, the ingest loop in `ingest.py`, the worker
loops in `workers.py`, barge-in in `interrupt.py` and control frames in
`session_control.py`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import Settings
from app.core.event_bus import EventBus
from app.kernel.taint import Source
from app.services.tools import ToolBroker
from app.services.voice.realtime import (
    interrupt,
    lifecycle,
    session_control,
    session_tasks,
    stt_task,
    workers,
)
from app.services.voice.realtime.barge_in import BargeInGate
from app.services.voice.realtime.chunker import SentenceChunker
from app.services.voice.realtime.greeting import GreetingOpts
from app.services.voice.realtime.ledger import SpokenLedger
from app.services.voice.realtime.metrics import MetricsSink, Throttle, TurnMetrics, UtteranceMarks
from app.services.voice.realtime.session_emit import RealtimeEmitter, ReplyStart, State
from app.services.voice.realtime.spoken_confirm import ConfirmWait
from app.services.voice.runtime import VoiceRuntime
from app.services.voice.turn import TurnEvent
from app.services.voice.vad_worker import VadWorker

if TYPE_CHECKING:
    from app.services.memory.runtime import MemoryRuntime

logger = logging.getLogger(__name__)


class _VoiceDetector(Protocol):
    """The slice of the VAD detector the session needs (lets tests inject a fake)."""

    def process_audio(self, samples: Any) -> list[TurnEvent]: ...

    def trailing_silence_ms(self) -> float: ...


async def _noop_prefetch(_user_text: str) -> str | None:
    """No memory context; the default when no context assembler is wired."""
    return None


class RealtimeSession(RealtimeEmitter):
    """Drives one live call over a WebSocket."""

    def __init__(
        self,
        ws: WebSocket,
        runtime: VoiceRuntime,
        bus: EventBus,
        settings: Settings,
        *,
        persona: Callable[[], str],
        metrics_sink: MetricsSink | None = None,
        prefetch: Callable[[str], Awaitable[str | None]] = _noop_prefetch,
        memory: MemoryRuntime | None = None,
        tools: ToolBroker | None = None,
        conn_id: str | None = None,
        clock: Callable[[], float] = time.perf_counter,
        detector: _VoiceDetector | None = None,
    ) -> None:
        self.ws = ws
        self.runtime = runtime
        self.settings = settings
        # A callable, not a string: the prompt is read per turn, so a personality edit
        # made mid-call applies from the next turn.
        self.persona = persona
        self.greeting = GreetingOpts()  # set from the call_start control message
        self.prefetch = prefetch
        self.memory = memory
        # None when no kernel or memory runtime is wired (and in most tests);
        # `respond` then makes a single pass without tools.
        self.tools = tools
        # Set while a spoken confirmation waits for its answer. The barge-in gate is
        # suspended for that window (see `spoken_confirm`); otherwise the "yes" would
        # cancel the tool round it is answering.
        self.awaiting_confirm: ConfirmWait | None = None
        self.clock = clock
        self._bus = bus
        self._source = f"voice.session.{conn_id or id(self)}"
        self._metrics_sink = metrics_sink or MetricsSink(settings.realtime.metrics_path)

        self._state = State.idle
        self._call_active = False
        self.audio_in: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.audio_out: asyncio.Queue[bytes | ReplyStart | None] = asyncio.Queue()
        self.utterance_q: asyncio.Queue[bytes | None] | None = None
        self._utterance_marks = UtteranceMarks()  # the open utterance's, while there is one
        self.history: list[dict[str, Any]] = []
        # Entries of `history` that carried what she read (a file read out, the opener's
        # recap of the last call): later turns read them as untrusted (`kernel/taint.py`).
        self.carried: list[tuple[Source, dict[str, Any]]] = []
        # What the turn's tools may have put in its reply (`tools/sources.py`), kept as
        # each one finishes so a reply cut short is marked too; and what searches returned.
        self.turn_sources: set[Source] = set()
        self.urls: frozenset[str] = frozenset()
        self.chunker = SentenceChunker(
            settings.realtime.chunk_max_chars,
            clause_chunking=settings.realtime.clause_chunking,
            min_clause_chars=settings.realtime.min_clause_chars,
        )
        self.barge = BargeInGate(clock)
        # The reply being played: its id (`ReplyStart`), which samples carry which
        # words, how far the client says it has played, and its history entry once
        # recorded, so an interruption during playback can cut it to what was heard.
        self.ledger = SpokenLedger()
        self.reply_id = 0
        self._reply_marked = False
        self.played_samples: int | None = None
        self.reply_entry: dict[str, Any] | None = None
        self.reply_caveats: list[dict[str, str]] = []  # shown after it (`claim_caveat.py`)
        self.audio_gen = 0
        self._last_partial = ""
        self.current_metrics: TurnMetrics | None = None  # in-flight turn (for barge-in)
        self._partial_throttle = Throttle(settings.realtime.partial_throttle_ms / 1000, clock)
        self._injected_detector = detector
        self._detector: _VoiceDetector | None = None
        self.vad_worker: VadWorker | None = None  # this call's voice-detection thread
        self._silero_model: Any = None
        self._stt_task: asyncio.Task[None] | None = None
        self._stt_tasks: set[asyncio.Task[None]] = set()  # every in-flight decode
        self.turn_in_flight = False  # a decode/reply is committed; new speech = barge-in
        self._respond_task: asyncio.Task[None] | None = None
        self._vad_pump_task: asyncio.Task[None] | None = None
        self._ingest_task: asyncio.Task[None] | None = None
        self._audio_out_task: asyncio.Task[None] | None = None
        self._warmup_task: asyncio.Task[None] | None = None
        self._greeting_task: asyncio.Task[None] | None = None
        self._unsub: Callable[[], None] | None = None

    @property
    def call_active(self) -> bool:
        """Whether a call is live on this session (`LiveCalls` counts these)."""
        return self._call_active

    # -- WebSocket loop --

    async def run(self) -> None:
        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    # Queue whole PCM16 samples only, and only during a call. The
                    # decoder also tolerates a stray byte; both guard the ingest loop,
                    # whose death would leave the call connected but deaf.
                    if self._call_active and len(data) >= 2:
                        self.audio_in.put_nowait(data if len(data) % 2 == 0 else data[:-1])
                    continue
                text = message.get("text")
                if text is not None:
                    control = session_control.parse(text)
                    if control is not None:
                        await session_control.dispatch(self, control)
        except WebSocketDisconnect:
            logger.info("client disconnected | %s", self._source)
        finally:
            await self._stop_call()

    # -- lifecycle --

    async def _on_call_start(self, message: dict[str, Any] | None = None) -> None:
        await lifecycle.start(self, message)

    async def _stop_call(self) -> None:
        await lifecycle.stop(self)

    async def _cancel_active_turn(self) -> None:
        tasks = [t for t in (self._respond_task, *self._stt_tasks) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._respond_task = None
        self._stt_task = None
        self._stt_tasks.clear()
        self.utterance_q = None
        self.turn_in_flight = False

    def _spawn_session_summary(self) -> None:
        """Summarize this session, and keep it for learning, in the background (off the
        hot path)."""
        if self.memory is None:
            return
        history = list(self.history)
        if not any(message["role"] == "user" for message in history):
            return  # nothing was said worth summarizing
        # Hand these to the app, not the session: they outlive the WebSocket, and the
        # lifespan drains `background_tasks` before shutdown. Otherwise closing the app
        # right after a call would close the database under them and lose the call's
        # summary and what learning will read.
        app = getattr(self.ws, "app", None)  # absent under the test doubles
        background: set[asyncio.Task[None]] | None = (
            getattr(app.state, "background_tasks", None) if app is not None else None
        )
        for work in (session_tasks.summarize_session, session_tasks.keep_for_learning):
            task = asyncio.create_task(work(self, history))
            if background is not None:
                background.add(task)
                task.add_done_callback(background.discard)
            task.add_done_callback(self._reap)

    # -- utterance lifecycle (called by the ingest loop / STT task) --

    def begin_utterance(self, *frames: bytes) -> None:
        """Open an utterance with the audio it starts with, oldest first."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        for frame in frames:
            queue.put_nowait(frame)
        self.utterance_q = queue
        marks = self._utterance_marks = UtteranceMarks()
        # Reap at creation so an STT failure is logged even after this reference
        # is overwritten by the next utterance (don't cancel a still-decoding one). The
        # one before it may still be decoding; it goes along so the two land in order.
        previous = self._stt_task
        answering = self.awaiting_confirm is not None
        task = asyncio.create_task(
            stt_task.run_stt(self, queue, marks, previous, answering=answering)
        )
        self._stt_task = task
        # Track every STT task, not just the newest: an earlier one can still be
        # decoding at teardown, and if it isn't cancelled it runs a whole turn after
        # the call ended, holding the models and mutating history.
        self._stt_tasks.add(task)
        task.add_done_callback(self._stt_tasks.discard)
        task.add_done_callback(self._reap)

    def end_utterance(self, last_voiced: float) -> None:
        """Close the utterance; `last_voiced` is when its last word was, on `clock`."""
        if self.utterance_q is not None:
            self._utterance_marks.mark_endpoint(self.clock(), last_voiced)
            self.utterance_q.put_nowait(None)  # finalize this utterance
            self.utterance_q = None
            # The turn is committed: decode and reply are coming. Mark it in flight and
            # arm barge-in here, not in `respond`, which only starts after the final
            # decode. In that gap a mid-sentence pause would otherwise open a second
            # utterance and run two replies at once over one shared chunker.
            self.turn_in_flight = True
            # Don't arm while a spoken confirmation is pending: this utterance is the
            # answer, and an armed gate would make the next one (the reply to a
            # re-ask) an interruption that cancels the confirmation.
            # `spoken_confirm.ask` re-arms the gate when the wait ends.
            if self.awaiting_confirm is None:
                self.barge.arm()
                # The reply before this turn is settled: heard to the end, or trimmed
                # when it was cut off. Talking over the next one used to rewrite it.
                self.reply_entry = None
                self.reply_caveats = []
                self.ledger.reset()
                self.played_samples = None

    def spawn_respond(self, user_text: str, marks: UtteranceMarks | None = None) -> None:
        previous = self._respond_task
        if previous is not None and not previous.done():
            previous.cancel()  # single-flight: never let two replies share the chunker
        self._respond_task = asyncio.create_task(workers.respond(self, user_text, marks))
        self._respond_task.add_done_callback(self._reap)  # log unexpected reply failures

    # -- barge-in --

    async def trigger_barge_in(self, *frames: bytes) -> None:
        await interrupt.barge_in(self, *frames)
