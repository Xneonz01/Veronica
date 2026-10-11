"""LocalAgreement-2 stable-prefix policy for streaming ASR.

Whisper re-decodes the growing audio buffer repeatedly and its output flickers
near the end. LocalAgreement-2 (UFAL `whisper_streaming`) tames this: a prefix is
emitted as confirmed only once two consecutive hypotheses agree on it, so partials
grow monotonically instead of rewriting themselves. This is our own focused
implementation of that idea — the upstream project is not vendored.
"""

from __future__ import annotations


def common_prefix(a: list[str], b: list[str]) -> list[str]:
    """Return the longest shared leading run of two word lists."""
    shared: list[str] = []
    for left, right in zip(a, b, strict=False):
        if left != right:
            break
        shared.append(left)
    return shared


class LocalAgreement:
    """Confirms a transcript prefix once two consecutive hypotheses agree on it."""

    def __init__(self) -> None:
        self._committed: list[str] = []
        self._prev: list[str] = []

    @property
    def confirmed_text(self) -> str:
        """The monotonically-growing confirmed transcript so far."""
        return " ".join(self._committed)

    def add_hypothesis(self, words: list[str]) -> list[str]:
        """Feed a fresh full hypothesis; return any newly-confirmed words.

        The confirmed prefix only ever extends — a later hypothesis that revises
        already-confirmed words does not retract them.
        """
        agreed = common_prefix(self._prev, words)
        self._prev = words
        if len(agreed) <= len(self._committed):
            return []
        newly = agreed[len(self._committed) :]
        self._committed = agreed
        return newly

    def reset(self) -> None:
        """Clear all state for the start of a new utterance."""
        self._committed = []
        self._prev = []
