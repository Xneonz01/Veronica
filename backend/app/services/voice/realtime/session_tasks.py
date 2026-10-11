"""Tasks at the edges of a call: the opening greeting and the end-of-call writes.

None of this runs during a turn (that loop is in `workers.py`): `welcome_back` fires
once before the user speaks, and the summary and the call kept for learning are written
once after it ends, off the latency path. Learning itself comes later, once the model
is free (`memory/call_queue.py`).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaUnreachableError,
)
from app.services.memory.address import ADDRESS_KEY, coerce_address, display_name
from app.services.memory.extraction import copies_example, said_words
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.voice.realtime.greeting import Greeter, TemplateGreeter
from app.telemetry import current_perf

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession

logger = logging.getLogger(__name__)

_GREETER: Greeter = TemplateGreeter()  # any Greeter fits here without touching call start

# The instruction's example, which a model can hand back as the summary of a call that
# never touched it: it would be said as "Last time, ..." at the next call. Seldom a real
# call's recap: "planning the vegetable garden" was one, for a call about tomatoes.
_SUMMARY_EXAMPLE = "we were comparing ferry timetables to the islands"
_SUMMARY_INSTRUCTION = (
    "Summarize this conversation in one short sentence describing what the user "
    "worked on or asked about, phrased to follow the word 'Last time,' (for example "
    f"'{_SUMMARY_EXAMPLE}'). No preamble, no quotes."
)


async def welcome_back(s: RealtimeSession) -> None:
    """Speak the greeting at call start. Never raises; the user can talk over it.

    A reply like any other: it has its own mark for the client, its ledger, and a gate
    that stays open while it plays, so talking over its end stops it and history keeps
    what was heard. A barge-in cancels it (it runs as its own task).
    """
    try:
        greeting = await _greeting_text(s)
        if not greeting:
            return
        gen = s.audio_gen
        s.begin_reply()
        s.barge.arm()  # the user can talk right over the opener
        await s.to_speaking()
        s.ledger.begin(greeting)
        async for pcm in s.runtime.tts.synthesize_stream(greeting, s.voice_id()):
            if gen != s.audio_gen:  # barged over the greeting -> stop; the new turn took over
                return
            await s.put_audio(pcm)
        s.reply_entry = {"role": "assistant", "content": greeting}
        s.history.append(s.reply_entry)
        s.carried.append(("memory", s.reply_entry))  # built from her memory of the last call
        s.barge.synthesis_done(s.ledger.total_samples)
    except Exception:  # a greeting must never break the call
        logger.exception("welcome-back greeting failed")
        s.barge.disarm()


async def _greeting_text(s: RealtimeSession) -> str:
    memory = s.memory
    if memory is None:
        return ""
    profile = await memory.service.get_profile()
    name = display_name(profile)
    address = coerce_address(profile.get(ADDRESS_KEY))  # honor "never" even in the opener
    summary = await memory.service.latest_summary()
    return _GREETER.build(
        name=name, summary=summary, now=datetime.now(), opts=s.greeting, address=address
    )


async def summarize_session(s: RealtimeSession, history: list[dict[str, Any]]) -> None:
    """Summarize the finished session into one sentence; store it (kind=summary)."""
    memory = s.memory
    if memory is None:
        return
    started = time.perf_counter()
    try:
        messages = [
            {"role": "system", "content": _SUMMARY_INSTRUCTION},
            {"role": "user", "content": _format_transcript(history)},
        ]
        parts = [
            chunk.content
            async for chunk in s.runtime.llm.stream_chat(
                messages, tools=None, model=s.settings.llm.model
            )
            if chunk.content
        ]
        summary = "".join(parts).strip()
        current_perf().log("memory.summary", (time.perf_counter() - started) * 1000.0)
        if summary and not copies_example(summary, said_words(history), (_SUMMARY_EXAMPLE,)):
            await memory.service.add(
                MemoryCreate(kind=MemoryKind.summary, content=summary, source="session")
            )
    except (OllamaUnreachableError, OllamaModelMissingError, OllamaModelFailedError) as exc:
        # Expected while the model is away or being pulled: one line, not a traceback
        # into the black box at every call's end.
        logger.warning("session summary skipped: %s", type(exc).__name__)
    except Exception as exc:  # best-effort recap; never disrupt teardown
        # The type only: a message can quote the recap, and so the call.
        logger.warning("session summary failed (%s)", type(exc).__name__)
        logger.debug("session summary failed", exc_info=True)


def _format_transcript(history: list[dict[str, Any]]) -> str:
    label = {"user": "User", "assistant": "Veronica"}
    return "\n".join(f"{label.get(m['role'], m['role'])}: {m['content']}" for m in history)


async def keep_for_learning(s: RealtimeSession, history: list[dict[str, Any]]) -> None:
    """Keep the finished call for learning, which reads it once the model is free: a
    model that can't be reached as the call ends no longer loses what was said."""
    memory = s.memory
    if memory is None:
        return
    try:
        await memory.calls.add(history)
    except Exception as exc:  # never disrupt teardown
        logger.warning("the call couldn't be kept for learning (%s)", type(exc).__name__)
