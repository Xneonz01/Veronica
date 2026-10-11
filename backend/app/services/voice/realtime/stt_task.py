"""The STT task: one utterance, from its audio to a turn.

Started for each utterance by `session.begin_utterance`. It streams the audio to
recognition and, on the final transcript, decides what the words are: the answer to a
spoken confirmation, an answer that came too late, the words before a newer utterance,
or a turn to reply to. The ingest loop never waits on it, so voice detection, and with
it barge-in, stays live while it decodes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.services.voice.realtime import spoken_confirm
from app.services.voice.realtime.confirm_reply import classify_answer
from app.services.voice.realtime.metrics import UtteranceMarks

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession

logger = logging.getLogger(__name__)


async def queue_iter(queue: asyncio.Queue[bytes | None]) -> AsyncIterator[bytes]:
    """Yield frames until the None sentinel; closes cleanly on consumer cancel."""
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item


async def run_stt(
    s: RealtimeSession,
    utterance_q: asyncio.Queue[bytes | None],
    marks: UtteranceMarks,
    previous: asyncio.Task[None] | None = None,
    *,
    answering: bool = False,
) -> None:
    """Stream the utterance to STT; on the final transcript, spawn the reply.

    `previous` is the utterance before this one. Speech picked up right after a pause
    interrupts while that one may still be decoding: its words go into history first,
    and only the newest utterance is answered, with both in view. Answering the older
    one started a reply while the user was still talking, and then a second.
    """
    final_text: str | None = None
    try:
        async for transcript in s.runtime.stt.stream_transcripts(queue_iter(utterance_q)):
            if transcript.is_final:
                final_text = transcript.text
                marks.stt_final = s.clock()
            else:
                await s.emit_partial(transcript.text)
    except Exception as exc:  # an STT failure must surface, not vanish silently
        logger.exception("STT failed")
        if s._stt_task is asyncio.current_task():  # a later utterance owns the turn
            _release_turn(s)
            await s.emit_error("stt_failed", str(exc))
            await s.to_listening()
        return
    if previous is not None and not previous.done():
        await asyncio.wait({previous})
    latest = s._stt_task is asyncio.current_task()
    if final_text and final_text.strip():
        await s.emit_final(final_text)
        if s.awaiting_confirm is not None and await spoken_confirm.answer(s, final_text):
            return  # consumed by the pending confirmation; not a new turn
        if answering:  # the question it answered closed while it was decoded
            _late_answer(s, final_text, marks, latest)
            return
        s.history.append({"role": "user", "content": final_text})
        if latest:
            s.spawn_respond(final_text, marks)  # `respond` owns the gate from here
        return
    # No usable transcript (silence, background noise, a cough). The turn is over
    # before it began and nothing downstream will release the gate, so do it here;
    # otherwise `ingest_loop` skips every later speech_start and the call goes deaf.
    # Unless a question came just before it: decoded while this one was, it went into
    # history unanswered, and a cough after "what time is it?" left it so for good.
    if latest:
        question = _unanswered(s)
        if question is not None:
            s.spawn_respond(question, marks)
        else:
            _release_turn(s)


def _late_answer(s: RealtimeSession, text: str, marks: UtteranceMarks, latest: bool) -> None:
    """Handle speech begun as an answer to a question that closed while it decoded.

    The card was tapped, or the window lapsed, while the user was still saying "yes".
    Taken as a new turn, it cancelled the reply that was going on about the action,
    unrecorded and unsilenced. A yes or no now answers nothing. Anything else is
    kept, and answered only if no reply is going on; otherwise the next turn sees it.
    """
    if classify_answer(text) is not None:
        return
    s.history.append({"role": "user", "content": text})
    if latest and s._respond_task is None:
        s.spawn_respond(text, marks)


def _unanswered(s: RealtimeSession) -> str | None:
    """The user's last words, if nothing has answered them and nothing is about to."""
    if s._respond_task is not None or s.awaiting_confirm is not None or not s.history:
        return None
    last = s.history[-1]
    return str(last["content"]) if last.get("role") == "user" else None


def _release_turn(s: RealtimeSession) -> None:
    """End a turn that never reached `respond` (no transcript, or STT failed)."""
    s.turn_in_flight = False
    s.barge.disarm()  # nothing is speaking, so the next speech is a fresh turn
