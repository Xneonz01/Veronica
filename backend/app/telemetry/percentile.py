"""The one rule her numbers are ranked by: a p90 or a p95 is the nearest rank, the smallest
value with at least that share of the values at or below it, so always one that happened.

The diagnostics summary, the loop-lag monitor, the call probe and the evaluations each
had a rule of their own, and the summary's p95 was one rank too high for some counts:
with 20 turns, it reported the slowest one (bug 26).
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def nearest_rank(ordered: Sequence[float], q: float) -> float:
    """The value at `q` (0 to 1) of the sorted, non-empty `ordered`."""
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]
