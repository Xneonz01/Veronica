"""The respond pipeline and the audio-out loop.

Free functions over a ``RealtimeSession``, kept apart from the session's state
machine. The ingest loop, in `ingest.py`, never blocks on STT or LLM work (the STT task,
in `stt_task.py`, spawns the respond task), so VAD stays live for barge-in throughout
the call.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaModelNoToolsError,
    OllamaUnreachableError,
)
from app.kernel.taint import Source, Taint
from app.services.memory.address import (
    classify_directive,
)
from app.services.tools import ToolOutcome
from app.services.voice.realtime import claim_caveat, tool_loop
from app.services.voice.realtime.metrics import TurnMetrics, UtteranceMarks
from app.services.voice.realtime.reply_cap import ReplyBudget, read_reply
from app.services.voice.realtime.session_emit import ReplyStart
from app.telemetry import current_perf

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession

logger = logging.getLogger(__name__)


def build_messages(
    persona: str, context: str | None, history: list[dict[str, Any]], cap_turns: int
) -> list[dict[str, Any]]:
    """System persona (plus the memory block, if any) followed by the recent turns."""
    system = persona if not context else f"{persona}\n\n{context}"
    recent = history[-(cap_turns * 2) :] if cap_turns > 0 else history
    return [{"role": "system", "content": system}, *recent]


async def audio_out_loop(s: RealtimeSession) -> None:
    """Drain synthesized PCM to the client as binary frames.

    A reply's mark goes out from here too, as a text frame, so it reaches the client
    between the last audio of one reply and the first of the next.
    """
    while True:
        item = await s.audio_out.get()
        if item is None:
            return
        try:
            if isinstance(item, ReplyStart):
                await s.ws.send_text(json.dumps({"type": "reply_audio", "reply": item.reply}))
            else:
                await s.ws.send_bytes(item)
        except Exception as exc:
            # The loop can't continue and nothing else would notice: the state machine
            # would keep cycling thinking -> speaking -> listening while the user hears
            # nothing for the rest of the call. Report it instead.
            logger.debug("audio send failed: %r", exc)
            await s.emit_error("audio_transport_failed", "audio stream closed")
            return


async def _apply_inline_address_correction(s: RealtimeSession, user_text: str) -> bool:
    """Apply a spoken form-of-address correction ("stop using my name") right away, and
    say whether it was written: her reply may then say she'll remember it.

    A cheap deterministic check writes the Tier-1 slot before this turn's prompt is
    assembled, so the reply already obeys it rather than waiting for the model to
    call `memory_save`. Failures are logged and ignored; a memory glitch must never
    break the reply.
    """
    memory = s.memory
    if memory is None:
        return False
    try:
        directive = classify_directive(user_text)
        if directive is not None:
            await memory.service.set_address_mode(directive)
            return True
    except Exception:
        logger.exception("inline address correction failed")
    return False


async def respond(s: RealtimeSession, user_text: str, marks: UtteranceMarks | None = None) -> None:
    """Stream the LLM reply, sentence-chunk it, synthesize, and meter the turn.

    `marks` holds when the user stopped speaking; a turn started without them is
    timed from `respond` on.
    """
    gen = s.audio_gen
    metrics = TurnMetrics(len(user_text), s.clock, marks or UtteranceMarks())
    s.current_metrics = metrics  # so a barge-in can record the interrupted turn
    s.begin_reply()
    first_audio = False
    try:
        await s.to_thinking()
        s.barge.arm()
        corrected = await _apply_inline_address_correction(s, user_text)
        try:
            prefetch_started = s.clock()
            context = await s.prefetch(user_text)
            metrics.prefetch_s = s.clock() - prefetch_started
            current_perf().log("memory.prefetch", metrics.prefetch_s * 1000.0)
        except Exception:  # memory is an enhancement — a glitch must not kill the reply
            logger.exception("memory prefetch failed; replying without it")
            context = None
        messages = build_messages(
            s.persona(), context, s.history, s.settings.realtime.history_turns
        )
        s.chunker.reset()
        ran: list[ToolOutcome] = []
        model = s.settings.llm.model
        budget = ReplyBudget(s.settings.realtime.reply_max_chars)
        # Set once a model call or the filler has ended: the next text starts a new
        # sentence, and the screen, which joins what it is sent, needs the space.
        gap = False

        async def speak_text(text: str) -> None:
            """Emit, chunk and synthesize; everything the turn says goes through here.

            One closure keeps ordering, metrics and the barge-in generation check in
            one place; the tool loop never touches TTS itself.
            """
            nonlocal first_audio, gap
            if gap and not text[:1].isspace():
                text = f" {text}"
            gap = False
            await s.emit_token(text)
            for sentence in s.chunker.push(text):
                first_audio = await _speak(s, sentence, gen, metrics, first_audio)

        async def speak_held() -> None:
            """Say what the chunker holds: the text it was waiting on isn't coming.

            It holds a sentence until more text arrives, which keeps a streamed "3",
            "." and "30" whole, and the last sentence of a model call has nothing
            after it.
            """
            nonlocal first_audio, gap
            for sentence in s.chunker.flush():
                first_audio = await _speak(s, sentence, gen, metrics, first_audio)
            gap = bool(s.chunker.spoken_text)

        if s.tools is None:  # no tool broker wired: a single pass without tools
            calling = s.runtime.llm.stream_chat(messages, tools=None, model=model)
            async with read_reply(calling, budget) as stream:
                async for chunk in stream:
                    if chunk.content:
                        metrics.mark_ttft()
                        await speak_text(chunk.content)
        else:
            # What she read this turn (the memory block) or repeated from an earlier
            # one, and what the user said in the window the prompt carries: an argument
            # copied from the first and not the second asks first.
            said = [m["content"] for m in messages if m["role"] == "user"]
            read: list[tuple[Source, str]] = [("memory", context)] if context else []
            read += [
                (source, entry["content"])
                for source, entry in s.carried
                if any(entry is message for message in messages)
            ]
            ran = await tool_loop.run_tool_rounds(
                s,
                s.tools,
                messages,
                model=model,
                metrics=metrics,
                speak=speak_text,
                speak_held=speak_held,
                budget=budget,
                taint=Taint.of(untrusted=read, trusted=said),
            )
        await speak_held()
        metrics.reply_capped = budget.capped
        # Recorded before anything else is said: talking over the caveat, or hanging up
        # during it, must find the reply already in history.
        reply = s.chunker.spoken_text
        s.reply_entry = {"role": "assistant", "content": reply}
        s.history.append(s.reply_entry)
        if ran:  # it may repeat what a tool returned
            s.carried.append(("tool_result", s.reply_entry))
        # The spoken "stop using my name" wrote the slot: a save, and a change of it.
        done = ("memory.save", "memory.correct") if corrected else ()
        await claim_caveat.settle(s, s.reply_entry, ran, done=done, memory=context)
    except OllamaModelMissingError as exc:
        await _abort_turn(s, "llm_model_missing", str(exc), model=exc.model)
        return
    except OllamaModelNoToolsError as exc:  # bug 45: another model is the fix
        await _abort_turn(s, "llm_no_tools", str(exc), model=exc.model)
        return
    except OllamaModelFailedError as exc:  # often memory another program holds
        await _abort_turn(s, "llm_cant_run", str(exc), model=exc.model)
        return
    except OllamaUnreachableError as exc:
        await _abort_turn(s, "llm_unreachable", str(exc))
        return
    except Exception as exc:  # TTS/synthesis or any other failure must surface, not hang
        logger.exception("reply pipeline failed")
        await _abort_turn(s, "reply_failed", str(exc))
        return
    metrics.mark_done(len(reply))
    # The turn is over before anything below awaits. Speech in that window found it
    # still open, interrupted a finished reply and recorded the turn a second time.
    s.current_metrics = None
    s.turn_in_flight = False
    # And no longer the reply an interruption cancels: that would cut the audio still
    # playing, and also this turn's own metrics row, mid-write.
    if s._respond_task is asyncio.current_task():
        s._respond_task = None
    # Not a disarm: the client is still playing the reply (`BargeInGate`).
    s.barge.synthesis_done(s.ledger.total_samples)
    this_reply = s.reply_id
    await s.emit_metrics(metrics)
    if s.reply_id == this_reply:  # not if the next reply has begun meanwhile
        await s.to_listening()


async def _abort_turn(s: RealtimeSession, kind: str, detail: str, **fields: str) -> None:
    """Recover from a failed reply and return to listening.

    Clears the in-flight turn and reports a recoverable error, so the call carries on
    instead of hanging.
    """
    _keep_spoken_text(s)
    s.current_metrics = None
    s.turn_in_flight = False
    if s._respond_task is asyncio.current_task():
        s._respond_task = None
    # Not a disarm: what she said before the failure may still be playing, and talking
    # over it has to stop it as it would a whole reply. With nothing queued it closes now.
    s.barge.synthesis_done(s.ledger.total_samples)
    await s.emit_error(kind, detail, **fields)
    await s.to_listening()


def _keep_spoken_text(s: RealtimeSession) -> None:
    """Record what she said before the turn ended early.

    Otherwise a reply that failed partway vanishes from history: the model loses track
    of what it just said, and the session summary and learning extractor miss it too.
    Only the sentences whose audio went out: the one that failed to be made was never
    said. Recorded as the reply, so an interruption while it plays trims it in place.
    """
    spoken = s.ledger.text_up_to(s.ledger.total_samples).strip()
    if spoken:
        s.reply_entry = {"role": "assistant", "content": f"{spoken}…"}
        s.history.append(s.reply_entry)


async def _speak(
    s: RealtimeSession, sentence: str, gen: int, metrics: TurnMetrics, first_audio: bool
) -> bool:
    metrics.mark_first_chunk()
    s.ledger.begin(sentence)
    async for pcm in s.runtime.tts.synthesize_stream(sentence, s.voice_id()):
        if gen != s.audio_gen:  # barged-in mid-synth -> drop stragglers
            return first_audio
        if not first_audio:
            metrics.mark_first_audio()
            first_audio = True
        # Queued before the state says so, so that by the time anything reads
        # "speaking", the gate knows her voice is going out. And on every chunk, not
        # only the turn's first: a spoken confirmation's wait moves the state to
        # listening and thinking, and she speaks again after it.
        await s.put_audio(pcm)
        await s.to_speaking()
    return first_audio
