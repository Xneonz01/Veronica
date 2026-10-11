"""Typed realtime voice events and the per-connection client forwarder.

The session publishes these on the in-process bus; a `ClientForwarder` bound to
one WebSocket relays the events for its own session (filtered by `source`, since
the bus is process-wide) to the client as JSON. Raw audio never rides the bus.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

from app.schemas.events import Event

logger = logging.getLogger(__name__)


class VoiceEvent:
    """Bus `type` strings for the realtime call lane."""

    state = "voice.state"
    asr_partial = "voice.asr.partial"
    asr_final = "voice.asr.final"
    assistant_token = "voice.assistant.token"
    assistant_caveat = "voice.assistant.caveat"
    barge_in = "voice.barge_in"
    playback_flush = "voice.playback_flush"
    metrics = "voice.metrics"
    error = "voice.error"


CALL_EVENT_TYPES: tuple[str, ...] = (
    VoiceEvent.state,
    VoiceEvent.asr_partial,
    VoiceEvent.asr_final,
    VoiceEvent.assistant_token,
    VoiceEvent.assistant_caveat,
    VoiceEvent.barge_in,
    VoiceEvent.playback_flush,
    VoiceEvent.metrics,
    VoiceEvent.error,
)

# Bus event type -> the client wire `type` field.
_WIRE_TYPE: dict[str, str] = {
    VoiceEvent.state: "veronica_state",
    VoiceEvent.asr_partial: "asr_partial",
    VoiceEvent.asr_final: "asr_final",
    VoiceEvent.assistant_token: "assistant_token",
    VoiceEvent.assistant_caveat: "assistant_caveat",
    VoiceEvent.barge_in: "barge_in",
    VoiceEvent.playback_flush: "playback_flush",
    VoiceEvent.metrics: "metrics",
    VoiceEvent.error: "error",
}


def state_event(state: str, *, source: str) -> Event:
    return Event(type=VoiceEvent.state, source=source, payload={"state": state})


def asr_partial_event(text: str, *, source: str) -> Event:
    return Event(type=VoiceEvent.asr_partial, source=source, payload={"text": text})


def asr_final_event(text: str, *, source: str) -> Event:
    return Event(type=VoiceEvent.asr_final, source=source, payload={"text": text})


def assistant_token_event(text: str, *, source: str) -> Event:
    return Event(type=VoiceEvent.assistant_token, source=source, payload={"text": text})


def assistant_caveat_event(text: str, *, source: str) -> Event:
    """What her reply said she did and she didn't (`tools/claims.py`), or passed on from
    a line written to her (`tools/relayed.py`): shown apart from her words, as chat shows
    it under the answer."""
    return Event(type=VoiceEvent.assistant_caveat, source=source, payload={"text": text})


def barge_in_event(*, source: str) -> Event:
    return Event(type=VoiceEvent.barge_in, source=source)


def playback_flush_event(*, source: str) -> Event:
    return Event(type=VoiceEvent.playback_flush, source=source)


def metrics_event(metrics: dict[str, Any], *, source: str) -> Event:
    return Event(type=VoiceEvent.metrics, source=source, payload=dict(metrics))


def error_event(kind: str, message: str, *, source: str, **detail: str) -> Event:
    """A kind the app has words for, with the values those words need (`model`)."""
    payload = {"kind": kind, "message": message, **detail}
    return Event(type=VoiceEvent.error, source=source, payload=payload)


class ClientForwarder:
    """Bus subscriber that relays this session's voice events to its client."""

    def __init__(self, websocket: WebSocket, source: str) -> None:
        self._ws = websocket
        self._source = source

    async def __call__(self, event: Event) -> None:
        if event.source != self._source:
            return  # another connection's event; the bus is process-wide
        wire_type = _WIRE_TYPE.get(event.type)
        if wire_type is None:
            return
        message: dict[str, Any] = {"type": wire_type, **event.payload}
        try:
            await self._ws.send_text(json.dumps(message))
        except Exception as exc:  # a dead socket must not break bus delivery
            logger.debug("client forward failed for %s: %r", event.type, exc)
