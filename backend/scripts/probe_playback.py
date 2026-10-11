"""The probe's stand-in for the app's speaker: it plays each reply in real time.

The server keeps listening for an interruption until a reply has been heard to the end,
and learns how far that is from the client (`playback_position`). A probe that asked
its next question as soon as the reply was made would talk over every answer, and each
question would be taken as an interruption. So it listens like a person with the app:
it reports how much of the reply has played, and waits for the reply to finish.
"""

from __future__ import annotations

PLAYBACK_RATE = 24_000  # samples a second of her voice


class Speaker:
    """How much of the current reply has played, as a player that started with its first
    audio and never ran ahead of what had arrived would have played it."""

    def __init__(self) -> None:
        self.reply: int | None = None
        self.received = 0
        self._played = 0.0
        self._at: float | None = None  # when `_played` was last brought up to date

    def begin(self, reply: int) -> None:
        """A new reply's mark: audio from here on is `reply`'s."""
        self.reply = reply
        self.received = 0
        self._played = 0.0
        self._at = None

    def receive(self, samples: int, now: float) -> None:
        if self.reply is None:
            return  # audio outside any reply isn't counted, as in the app
        self.played(now)
        if self._at is None:
            self._at = now  # playback starts with the first audio
        self.received += samples

    def played(self, now: float) -> int:
        """Samples played by `now`. A player that runs out of audio waits for more."""
        if self._at is not None:
            self._played = min(self.received, self._played + (now - self._at) * PLAYBACK_RATE)
            self._at = now
        return int(self._played)

    def finished(self, now: float) -> bool:
        return self.reply is None or self.played(now) >= self.received
