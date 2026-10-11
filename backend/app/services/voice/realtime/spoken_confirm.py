"""Spoken confirmation: ask permission out loud and hear the answer.

By default `files.write`, `apps.open` and `memory.forget` need confirmation. On screen
that is a card, but on a call the user may be across the room, so the question is
spoken and the next utterance is taken as the answer.

This is the one relaxation of the call loop. `respond` arms the barge-in gate for
the whole turn and a confirmation blocks inside the turn, so the user's "yes" would
trigger `should_interrupt` and cancel the tool round it is answering. While a
confirmation is pending the gate is off (nothing is being said; the call is
waiting) and the next final transcript is routed here instead of starting a new
turn. Nothing else about barge-in changes, and the gate is re-armed when the wait
ends.

Consent is never inferred from silence: the kernel's window fails closed, and an
unanswered question expires with a spoken notice so the user knows it lapsed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.kernel.errors import Code
from app.services.voice.realtime.confirm_reply import classify_answer, looks_like_a_new_request

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession

logger = logging.getLogger(__name__)

# Said first when a call asks because of what she read (`kernel.FROM_READING`): where
# the words came from, which is what the check knows, and not who wanted it done.
_FROM_READING = "The words for this came from something I read, not from you."


@dataclass
class ConfirmWait:
    """A pending spoken confirmation, owned by the session for its lifetime."""

    token: str
    capability: str
    summary: str
    reasked: bool = False
    answered: asyncio.Event = field(default_factory=asyncio.Event)
    # Set when the user's reply was not an answer at all; the caller adds it to the
    # call history once the wait is closed.
    carried_over: str | None = None


def spoken_summary(capability: str, summary: str, why: str | None = None) -> str:
    """Rephrase the kernel's summary as a spoken question.

    The kernel's summary is written for a screen ("write 19 characters to
    C:\\long\\path\\notes.md"). Aloud, a full path is unusable and a character count is
    noise; the filename is the part a person can act on.

    With `why`, the kernel asked because an argument came from something she read,
    for an action that would otherwise have run unasked; the question says so first.
    """
    question = _question(capability, summary)
    return f"{_FROM_READING} {question}" if why else question


def _question(capability: str, summary: str) -> str:
    name = _filename_in(summary)
    if capability == "files.write":
        if not name:
            return "Want me to write that?"
        # Aloud, this is the only question there is: it must say what a yes costs.
        verb = "replace" if summary.startswith("replace ") else "write to"
        return f"That'll {verb} {name}. Want me to?"
    if capability == "apps.open":
        target = summary.replace("open", "", 1).strip() or "that"
        return f"Shall I open {target}?"
    if capability.startswith("memory."):
        # Written to be said: "forget “User sees the dentist on Tuesdays”".
        return f"Shall I {summary}?"
    return f"Shall I go ahead with {capability.split('.')[-1]}?"


def _filename_in(summary: str) -> str:
    """The last path-ish token, reduced to its filename. Never speak a full path."""
    for token in reversed(summary.split()):
        cleaned = token.strip("'\"“”.,")
        if "/" in cleaned or "\\" in cleaned:
            return Path(cleaned).name
        if "." in cleaned and not cleaned.endswith("."):
            return cleaned
    return ""


async def answer(s: RealtimeSession, text: str) -> bool:
    """Route one utterance to the pending confirmation; True if it was consumed.

    Returns False only when no confirmation is pending, so the caller starts a normal
    turn. A reply that isn't an answer is consumed too: a short unclear one gets one
    re-ask; otherwise the confirmation is denied and the text kept in `carried_over`.
    """
    wait = s.awaiting_confirm
    if wait is None:
        return False
    verdict = classify_answer(text)

    if verdict is None and not looks_like_a_new_request(text) and not wait.reasked:
        wait.reasked = True
        await say_aside(s, "Sorry — was that a yes?")
        return True

    if verdict is None:
        # They moved on. Deny it (fail-closed, and the audit records a clear denial)
        # and hand what they actually said back to the caller.
        logger.info("spoken confirm abandoned for %s", wait.capability)
        wait.carried_over = text
        _resolve(s, wait, approved=False)
        return True

    _resolve(s, wait, approved=verdict)
    return True


def _resolve(s: RealtimeSession, wait: ConfirmWait, *, approved: bool) -> None:
    """Answer the kernel's pending token.

    Tokens are single-use, so if the on-screen card answers at the same time, the
    first answer wins and the other is a no-op.
    """
    if s.tools is not None:
        s.tools.resolve_confirmation(wait.token, approved, via="voice")
    wait.answered.set()


async def say_aside(s: RealtimeSession, text: str) -> None:
    """Speak one line now, outside the chunker: it isn't part of the model's reply, and
    the chunker would hold its last sentence until more text arrived. Nor is it among
    the reply's words: its ledger span has no text (a question, a claim's caveat,
    which `claim_caveat.py` puts in history itself)."""
    # The question is the first thing heard in its turn, so it starts the turn's first
    # audio, as the acknowledgement for a slow tool does. Otherwise the turn's timings
    # would include however long the user took to answer.
    metrics = s.current_metrics
    if metrics is not None:
        metrics.mark_first_chunk()
    gen = s.audio_gen
    # In the reply's ledger with no text: its audio is part of what the client plays,
    # and the positions it reports count it, but it isn't part of the reply.
    s.ledger.begin("")
    async for pcm in s.runtime.tts.synthesize_stream(text, s.voice_id()):
        if gen != s.audio_gen:  # barged over — stop, the new turn owns the floor
            return
        if metrics is not None:
            metrics.mark_first_audio()
        await s.put_audio(pcm)


async def ask(s: RealtimeSession, request_id: str, running: asyncio.Task[Any]) -> str | None:
    """Speak the question, then wait until the confirmation is resolved.

    Returns when the kernel's call resolves: by voice, by the on-screen card, or by
    its own timeout. Nothing is decided here; a spoken answer reaches the kernel via
    `answer`, and an unanswered window expires, fail-closed.

    Returns an utterance that turned out to be a new request rather than an answer,
    so the caller can keep it once the confirmation is closed.
    """
    view = await s.tools.prompt_for(request_id, running) if s.tools is not None else None
    if view is None:  # never appeared, or it resolved before we looked
        return None
    wait = ConfirmWait(token=view.token, capability=view.capability, summary=view.summary)
    s.awaiting_confirm = wait
    # Disarm the gate so the reply is taken as an answer, not an interruption.
    s.barge.disarm()
    await say_aside(s, spoken_summary(view.capability, view.summary, view.why))
    await s.to_listening()

    answered = asyncio.ensure_future(wait.answered.wait())
    try:
        # Either the user answers, or the kernel's own window closes and `running`
        # returns a denial. Whichever lands first ends the wait.
        await asyncio.wait({answered, running}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        answered.cancel()
    # No spoken answer: the card answered, or the window closed. Only a refusal or a
    # lapse is left undone; approved on screen, the action went ahead, and she used to
    # say she'd leave it while it did.
    if not wait.answered.is_set() and _turned_down(running):
        await say_aside(s, "I'll leave that then.")
    await s.to_thinking()
    s.continue_reply()
    s.barge.arm()
    return wait.carried_over


def _turned_down(running: asyncio.Task[Any]) -> bool:
    """Whether the action was declined or lapsed, rather than approved on screen."""
    if not running.done() or running.cancelled() or running.exception() is not None:
        return False
    return getattr(running.result(), "code", "") in (Code.confirm_denied, Code.confirm_timeout)
