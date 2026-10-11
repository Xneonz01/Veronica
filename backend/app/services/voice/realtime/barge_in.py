"""Barge-in gate: detect the user interrupting while Veronica holds the floor.

Sensitivity comes from the shared VAD `min_speech_ms` (the detector only emits
`speech_start` after that much sustained voiced audio) plus the mic's echo
cancellation, which keeps Veronica's own output from interrupting her. The gate is
armed from the end of the user's utterance, so the decode and the reply can be
interrupted, and fires on the next `speech_start`.

It closes when she has been heard to the end, not when her voice has been made. It used
to close when the last sentence was synthesised, and the client can play for seconds
after that: talking over the rest of a reply didn't stop it. So a finished reply hands
the gate the length of its audio (`synthesis_done`), and the gate stays armed until the
client reports having played it all. Reports can stop (a client that sends none, a
socket going away), so there is a ceiling that always closes it: audio can't take
longer than itself to play, plus a margin for the client's buffer and the socket.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.services.voice.audio import PLAYBACK_SAMPLE_RATE
from app.services.voice.turn import TurnEvent

# Slack for the client's buffer and the socket, past the audio's own length or the last
# position report. Generous on purpose: closing early leaves her talking over the user,
# and closing late only makes speech after a finished reply count as an interruption.
_PLAYOUT_MARGIN_S = 0.5


class BargeInGate:
    """Fires once when the user starts speaking while the gate is armed."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._armed = False
        self._playback_started_at: float | None = None
        self._total_samples: int | None = None
        self._played_samples = 0
        self._reported_at: float | None = None

    @property
    def armed(self) -> bool:
        """Whether speech now would still interrupt something."""
        if not self._armed:
            return False
        if self._total_samples is None:
            return True  # still thinking, or her voice is still being made
        if self._played_samples >= self._total_samples:
            return False  # heard to the end
        # While the client keeps reporting, a pause in her speech (the next sentence
        # still being made) doesn't close the gate; the ceiling covers a client that
        # stopped reporting or never did.
        latest = max(self._deadline(), (self._reported_at or float("-inf")) + _PLAYOUT_MARGIN_S)
        return self._clock() < latest

    @property
    def playing(self) -> bool:
        """Whether her voice may be coming out of the speaker: audio of this reply has
        gone out and hasn't been heard to the end. The state can already say
        "listening" then, since it follows synthesis, not the speaker."""
        return self._playback_started_at is not None and self.armed

    def arm(self) -> None:
        self._armed = True
        self._reset_playout()

    def disarm(self) -> None:
        """The turn is over: an interruption, a failure, the end of the call."""
        self._armed = False
        self._reset_playout()

    def playback_started(self) -> None:
        """Her first audio of this reply is on its way; the ceiling counts from here."""
        if self._playback_started_at is None:
            self._playback_started_at = self._clock()

    def synthesis_done(self, total_samples: int) -> None:
        """All of the reply's audio is made and queued. Not a disarm: the user is still
        hearing it."""
        self._total_samples = max(0, total_samples)

    def note_played(self, samples: int) -> None:
        """The client's position in this reply. It never goes back: a stale or reordered
        report can't reopen audio already heard."""
        self._played_samples = max(self._played_samples, samples)
        self._reported_at = self._clock()

    def should_interrupt(self, events: list[TurnEvent]) -> bool:
        """Return True (and disarm) if the user started speaking while armed."""
        if self.armed and TurnEvent.speech_start in events:
            self.disarm()
            return True
        return False

    def _reset_playout(self) -> None:
        self._playback_started_at = None
        self._total_samples = None
        self._played_samples = 0
        self._reported_at = None

    def _deadline(self) -> float:
        """When the reply must have finished playing, whatever the client has said."""
        started = self._playback_started_at
        if started is None:
            return float("-inf")  # no audio was ever queued: nothing left to interrupt
        duration = (self._total_samples or 0) / PLAYBACK_SAMPLE_RATE
        return started + duration + _PLAYOUT_MARGIN_S
