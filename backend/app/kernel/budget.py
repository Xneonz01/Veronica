"""How often a capability may run: per turn and per minute.

A page can ask her to "read these fifty pages", and every call the model writes runs.
Each read is a request out (a channel, and a quota against a key), and fifty of them
hold the turn for half an hour. So each capability that reaches the network has a
ceiling per turn and per minute, and every turn a ceiling on all its calls.

`allows` only reads, so the verdict (`kernel/verdict.py`) can say "refused" before
anyone is asked. `take` checks and counts in one step, with nothing awaited between,
right before a call runs: only a call that runs is counted, whether it ran unasked or
after a yes, and two calls of one round can't both slip under the last place.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping

_WINDOW_S = 60.0
_TURNS_KEPT = 64  # turns whose counts are remembered; older ones are long over


class Budget:
    """Per-turn and per-minute ceilings, by capability, and a per-turn total."""

    def __init__(
        self,
        *,
        per_turn: Mapping[str, int],
        per_minute: Mapping[str, int],
        turn_total: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_turn = dict(per_turn)
        self._per_minute = dict(per_minute)
        self._turn_total = turn_total
        self._clock = clock
        self._turns: OrderedDict[str, dict[str, int]] = OrderedDict()
        self._recent: dict[str, deque[float]] = {}

    def allows(self, capability: str, turn: str | None) -> bool:
        """Whether one more call of `capability` in `turn` would stay within every ceiling."""
        if turn is not None:
            counts = self._turns.get(turn, {})
            if sum(counts.values()) >= self._turn_total:
                return False
            ceiling = self._per_turn.get(capability)
            if ceiling is not None and counts.get(capability, 0) >= ceiling:
                return False
        limit = self._per_minute.get(capability)
        return limit is None or len(self._window(capability)) < limit

    def take(self, capability: str, turn: str | None) -> bool:
        """Count one call that is about to run; False, counting nothing, if it can't."""
        if not self.allows(capability, turn):
            return False
        if turn is not None:
            counts = self._turns.setdefault(turn, {})
            self._turns.move_to_end(turn)
            counts[capability] = counts.get(capability, 0) + 1
            while len(self._turns) > _TURNS_KEPT:
                self._turns.popitem(last=False)
        if capability in self._per_minute:
            self._window(capability).append(self._clock())
        return True

    def _window(self, capability: str) -> deque[float]:
        """The times `capability` ran in the last minute, older ones dropped."""
        recent = self._recent.setdefault(capability, deque())
        cutoff = self._clock() - _WINDOW_S
        while recent and recent[0] <= cutoff:
            recent.popleft()
        return recent
