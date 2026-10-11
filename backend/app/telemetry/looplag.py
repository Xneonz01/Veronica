"""Event-loop lag: how late the loop gets round to a task that asked to wake on time.

A coroutine that does blocking work without awaiting holds up everything else on the
loop, the voice pipeline included, and no other number shows it: each stage's own
timing looks fine while the call stutters and barge-in reacts late. This task asks
to wake every 100 ms and records how late it actually woke; the overshoot is time
the loop spent unable to run anything else.

Percentiles cover a fixed ring of the latest samples (a minute at the default
interval), so a long run can't grow memory through it. The worst stall is tracked
separately since the last `clear()`, so a spike early in a long call isn't lost when
it rotates out of the ring. On Windows the default timer ticks every ~15.6 ms, so
an idle loop already shows several milliseconds; a blocked one shows tens or hundreds.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque

from app.telemetry.percentile import nearest_rank

INTERVAL_S = 0.1
RING = 600  # a minute of history at the default interval

Snapshot = dict[str, float | int | None]


class LoopLagMonitor:
    """Samples how late the event loop wakes a sleeping task."""

    def __init__(self, interval_s: float = INTERVAL_S, ring: int = RING) -> None:
        self._interval = interval_s
        self._samples: deque[float] = deque(maxlen=ring)
        self._worst = 0.0
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start sampling on `loop`. A second call does nothing."""
        if self._task is None:
            self._task = loop.create_task(self._run(), name="loop-lag")

    def stop(self) -> None:
        """Cancel the sampling task; the loop finishes it on its next pass."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()

    def clear(self) -> None:
        """Forget the history, so the next snapshot covers only what follows."""
        self._samples.clear()
        self._worst = 0.0

    def snapshot(self) -> Snapshot:
        """p50 and p95 over the ring and the worst stall since `clear()`, in ms.

        None before the first sample: a zero would read as a measurement.
        """
        ordered = sorted(self._samples)
        if not ordered:
            return {
                "loop_lag_p50_ms": None,
                "loop_lag_p95_ms": None,
                "loop_lag_max_ms": None,
                "loop_lag_samples": 0,
            }
        return {
            "loop_lag_p50_ms": _ms(statistics.median(ordered)),
            "loop_lag_p95_ms": _ms(nearest_rank(ordered, 0.95)),
            "loop_lag_max_ms": _ms(self._worst),
            "loop_lag_samples": len(ordered),
        }

    async def _run(self) -> None:
        while True:
            before = time.perf_counter()
            await asyncio.sleep(self._interval)
            # The overshoot, not the elapsed time: anything past the interval is time
            # the loop couldn't get to this task.
            lag = max(0.0, time.perf_counter() - before - self._interval)
            self._samples.append(lag)
            self._worst = max(self._worst, lag)


class NullLoopLag:
    """Stands in when telemetry is off: nothing runs and there is nothing to report."""

    running = False

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        return None

    def stop(self) -> None:
        return None

    def clear(self) -> None:
        return None

    def snapshot(self) -> Snapshot:
        return {}


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 1)
