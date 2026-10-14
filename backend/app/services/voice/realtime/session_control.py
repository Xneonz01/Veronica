"""Control frames from the client: parsed defensively, and the few it knows acted on.

The client isn't the server's to trust, so a frame is untrusted input. Malformed JSON, a
bare array, a value of the wrong type or an action it doesn't know is ignored, never an
error: any of those escaping would close the socket and end the call.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, TypeGuard

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession

logger = logging.getLogger(__name__)


def parse(text: str) -> dict[str, Any] | None:
    """A control frame as a dict, or None for anything else.

    Junk JSON raises `JSONDecodeError`, and a bare array or string has no `.get`.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        logger.debug("dropped a malformed control frame")
        return None
    return payload if isinstance(payload, dict) else None


async def dispatch(s: RealtimeSession, message: dict[str, Any]) -> None:
    action = message.get("action")
    if action == "call_start":
        await s._on_call_start(message)
    elif action == "call_stop":
        await s._stop_call()
    elif action == "playback_position":
        note_playback(s, message)
    elif action == "call_options":
        note_options(s, message)


def note_options(s: RealtimeSession, message: dict[str, Any]) -> None:
    """What the client's call settings say: `expressive`, whether her voice makes the
    sounds its tags ask for. Sent with `call_start` and again whenever it changes, and
    read from the next reply on."""
    expressive = message.get("expressive")
    if isinstance(expressive, bool):
        s.expressive = expressive


def note_playback(s: RealtimeSession, message: dict[str, Any]) -> None:
    """How much of a reply the client has played: `reply` is the id the server gave it
    (`reply_audio`), `samples` counts 24 kHz samples played since that mark.

    A report about an earlier reply is ignored, and a position never goes back.
    """
    reply, samples = message.get("reply"), message.get("samples")
    if not (_count(reply) and _count(samples)) or reply != s.reply_id:
        return
    s.played_samples = max(s.played_samples or 0, samples)
    s.barge.note_played(samples)


def _count(value: object) -> TypeGuard[int]:
    # `True` is an int to Python and to `json`; as a count it is a malformed frame.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
