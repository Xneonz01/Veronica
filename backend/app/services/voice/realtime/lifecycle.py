"""Call lifecycle: start, warm the models up, go live, and stop.

Free functions over a `RealtimeSession`, like `workers.py`, so that `session.py`
stays the state machine and the utterance/turn plumbing. Everything here runs once
per call, never per turn.

The ordering inside `start` and `warmup_and_listen` matters. State is emitted before
the heavy model load so the UI always has feedback, and a warmup failure unsubscribes
the forwarder itself because `stop` returns early on an inactive call and would leave
the subscription behind.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.adapters import model_files
from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaUnreachableError,
)
from app.core.gpu_manager import GpuMode
from app.services.voice.realtime import card_room, ingest, session_tasks, workers
from app.services.voice.realtime.greeting import parse_opts
from app.services.voice.realtime.session_emit import State
from app.services.voice.realtime.voice_events import ClientForwarder
from app.services.voice.turn import SileroTurnDetector, silero_loader
from app.services.voice.vad_worker import VadWorker
from app.telemetry import current_blackbox, current_loop_lag, current_perf
from app.telemetry.process import cuda_used_mb, handle_count, rss_mb, thread_count

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession, _VoiceDetector

logger = logging.getLogger(__name__)

# The models a call's GPU mode loads, which the app has words for (`callErrors.ts`):
# both voice models and the memory model. End-of-turn detection is done without.
_CALL_NEEDS = frozenset({"stt", "tts", model_files.MEMORY_MODEL})


async def start(s: RealtimeSession, message: dict[str, Any] | None = None) -> None:
    if s._call_active:
        return
    s._call_active = True
    s.runtime.calls.started(s)  # the speech models stay while it is live
    s.greeting = parse_opts((message or {}).get("greeting"))  # frontend-owned
    logger.info("call_start | %s", s._source)

    # Reset per-call state. Fresh audio queues so frames captured while we
    # warm up land here and get dropped before we actually start listening.
    s.audio_in = asyncio.Queue()
    s.audio_out = asyncio.Queue()
    s.utterance_q = None
    s.history = []
    s.chunker.reset()
    s.barge.disarm()
    s.audio_gen = 0
    s.reply_id = 0
    s.begin_reply()  # a clean ledger, no position and no reply entry from the last call
    s._last_partial = ""
    s.current_metrics = None

    # Subscribe the forwarder and emit `connecting` right away, then do the heavy
    # model load off the receive loop. A cold load takes tens of seconds; blocking
    # here would freeze the call and leave the client with no feedback.
    forwarder = ClientForwarder(s.ws, s._source)
    s._unsub = s._subscribe_forwarder(forwarder)
    await s._set_state(State.connecting)
    s._warmup_task = asyncio.create_task(warmup_and_listen(s))


async def warmup_and_listen(s: RealtimeSession) -> None:
    """Load models + build the detector, then go live. Off the hot path."""
    started = time.perf_counter()
    try:
        # A separate `warming_up` state lets the UI explain the cold model load
        # (tens of seconds on the first call).
        await s._set_state(State.warming_up)
        if not await _room_on_the_card(s):
            _give_up(s)
            return
        await s.runtime.manager.set_mode(GpuMode.conversation)
        await s.runtime.tts.activate_voice(s.voice_id())
        await prewarm_tts(s)
        await prewarm_llm(s)
        s._detector = await build_detector(s)
        s.vad_worker = VadWorker(s._detector, s.clock)
    except Exception as exc:  # any failure is reported; a call must not die silently
        logger.exception("call warmup failed")
        await _warmup_failed(s, exc)  # said while still subscribed
        _give_up(s)
        return
    current_perf().log("voice.warmup", (time.perf_counter() - started) * 1000.0)
    _record_call("call.started")
    # The lag reported at `call.stopped` should describe the conversation; the model
    # load is already timed as `voice.warmup`.
    current_loop_lag().clear()
    s._drain_audio_in()  # discard audio captured while warming up
    s._vad_pump_task = asyncio.create_task(ingest.vad_pump(s))
    s._vad_pump_task.add_done_callback(s._reap)
    s._ingest_task = asyncio.create_task(ingest.ingest_loop(s))
    s._ingest_task.add_done_callback(s._reap)  # a dead loop must not stay silent
    s._audio_out_task = asyncio.create_task(workers.audio_out_loop(s))
    s._audio_out_task.add_done_callback(s._reap)
    # Its own task, so a barge-in can cancel it mid-sentence.
    s._greeting_task = asyncio.create_task(greet(s))
    s._greeting_task.add_done_callback(s._reap)


async def _room_on_the_card(s: RealtimeSession) -> bool:
    """Whether the graphics card has room for the call (bug 46: without it the call was
    paged in and out and stopped answering, and she couldn't say why), said to the caller
    when it hasn't, or has only just. A room that can't be judged lets the call go ahead."""
    room = await card_room.measure(s.settings)
    if room is None or room.verdict == "fits":
        return True
    if room.verdict == "small":
        await s.emit_error(
            "card_small",
            f"the card has {room.total_mib} MiB, and a call needs {room.card_needs_mib}",
            total_mib=str(room.total_mib),
            needs_mib=str(room.card_needs_mib),
        )
        return False
    if room.verdict == "full":
        await s.emit_error(
            "card_full",
            f"the card has {room.free_mib} MiB free, and a call needs {room.needs_mib}",
            free_mib=str(room.free_mib),
            needs_mib=str(room.needs_mib),
        )
        return False
    await s.emit_error(
        "card_tight", f"the card has {room.free_mib} MiB free, a call adds {room.adds_mib}"
    )
    return True


def _give_up(s: RealtimeSession) -> None:
    """Let go of a call that won't go live. No loops were started, and `stop` returns early
    once `_call_active` is False, so the forwarder is unsubscribed here."""
    s._call_active = False  # stop run() buffering audio into a queue nothing drains
    if s._unsub is not None:
        s._unsub()
        s._unsub = None
    s.runtime.calls.ended(s.settings.realtime.release_models_after_s)


async def _warmup_failed(s: RealtimeSession, exc: Exception) -> None:
    """Say why the call can't start: a model it needs that isn't on disk by name, found
    as the start-up finds it (bug 44: it read as "check that the backend is running"),
    and anything else in general words. Only a model the warm-up would still have loaded
    is named: one already in memory, or one no mode loads (the memory model of a memory
    file that couldn't be opened), didn't stop it, and installing it wouldn't help."""
    try:
        waiting = s.runtime.manager.to_load(GpuMode.conversation) & _CALL_NEEDS
        absent = await asyncio.to_thread(model_files.missing, s.settings) if waiting else []
    except Exception:  # the check itself failing leaves the general words
        logger.warning("the models a call needs couldn't be checked", exc_info=True)
        waiting, absent = set(), []
    needed = [weights for weights in absent if weights.code in waiting]
    if needed:
        codes = ",".join(weights.code for weights in needed)
        await s.emit_error("models_missing", model_files.sentence(needed), models=codes)
    else:
        await s.emit_error("warmup_failed", str(exc))


async def greet(s: RealtimeSession) -> None:
    """Say the greeting, then settle to listening."""
    await session_tasks.welcome_back(s)  # never raises; the user can talk over it
    # Settle to listening only if the greeting wasn't barged over; otherwise this
    # would clobber a live turn with an invalid `thinking -> listening` transition.
    if s._state is State.speaking or s._state is State.warming_up:
        await s._set_state(State.listening)


async def build_detector(s: RealtimeSession) -> _VoiceDetector:
    if s._injected_detector is not None:
        return s._injected_detector
    if s._silero_model is None:
        s._silero_model = await asyncio.to_thread(silero_loader())
    reset = getattr(s._silero_model, "reset_states", None)
    if callable(reset):
        reset()
    arbiter = await s.runtime.turn_arbiter()
    return SileroTurnDetector(s.settings.vad, s._silero_model, arbiter)


async def prewarm_tts(s: RealtimeSession) -> None:
    # Run one synthesis so the first real turn isn't paying Chatterbox's cold
    # first-generate cost (the dominant first-audio latency term otherwise).
    async for _ in s.runtime.tts.synthesize_stream("Ready.", s.voice_id()):
        pass


async def prewarm_llm(s: RealtimeSession) -> None:
    try:
        await s.runtime.llm.warm(s.settings.llm.model)
    except OllamaUnreachableError:
        logger.warning("Ollama unreachable during prewarm")
    except (OllamaModelMissingError, OllamaModelFailedError) as exc:
        # The call goes live all the same, and its first turn says what to do: which
        # model to pull, or what to close (bug 45: a model that couldn't run failed
        # the warm-up, as "check that the backend is running").
        logger.warning("%s", exc)


async def stop(s: RealtimeSession) -> None:
    if not s._call_active:
        return
    s._call_active = False
    s._spawn_session_summary()  # background recap for next time (off the hot path)
    if s._unsub is not None:
        s._unsub()
        s._unsub = None
    # The loops before the turn. While the ingest loop runs it can take speech already
    # scored as an interruption and open an utterance, and cancelling the turn awaits:
    # an STT task opened in that wait was never cancelled, and decoded after the call.
    loops = [
        task
        for task in (
            s._warmup_task,
            s._greeting_task,
            s._vad_pump_task,
            s._ingest_task,
            s._audio_out_task,
        )
        if task is not None
    ]
    for task in loops:
        task.cancel()
    if loops:
        await asyncio.gather(*loops, return_exceptions=True)
    s._warmup_task = None
    s._greeting_task = None
    s._vad_pump_task = None
    s._ingest_task = None
    s._audio_out_task = None
    await s._cancel_active_turn()
    timing: dict[str, float] = {}
    if s.vad_worker is not None:
        # Last, when nothing feeds or reads it; the join can take a moment.
        await asyncio.to_thread(s.vad_worker.close)
        timing = s.vad_worker.timing()
        s.vad_worker = None
    s._state = State.idle  # socket may be closing; do not emit
    _record_call("call.stopped", **timing)
    s.runtime.calls.ended(s.settings.realtime.release_models_after_s)


def _record_call(event: str, **extra: float) -> None:
    """A call boundary, with the readings that show resources creeping up across calls.

    `call.stopped` also carries how long the event loop was held up during the call,
    and how long voice detection took per frame.
    """
    fields: dict[str, Any] = {
        "vram_used_mb": cuda_used_mb(),
        "rss_mb": rss_mb(),
        "threads": thread_count(),
        "handles": handle_count(),
    }
    if event == "call.stopped":
        fields.update(current_loop_lag().snapshot())
    fields.update(extra)
    current_blackbox().record("info", event, **fields)
