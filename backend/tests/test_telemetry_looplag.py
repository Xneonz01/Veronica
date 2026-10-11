"""The event-loop lag monitor: it sees a blocked loop, stays bounded and stops cleanly."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from app.services.voice.realtime import lifecycle
from app.telemetry import current_loop_lag, install, uninstall
from app.telemetry.blackbox import BlackBox
from app.telemetry.looplag import LoopLagMonitor, NullLoopLag
from app.telemetry.perf import PerfLogger
from app.telemetry.runctx import RunContext


async def _let_it_sample(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def test_a_blocked_loop_shows_up_as_lag() -> None:
    monitor = LoopLagMonitor(interval_s=0.01)
    monitor.start(asyncio.get_running_loop())
    try:
        await _let_it_sample(0.05)
        time.sleep(0.15)  # blocking on purpose: nothing else can run on the loop
        await _let_it_sample(0.05)
        reading = monitor.snapshot()
    finally:
        monitor.stop()
    worst = reading["loop_lag_max_ms"]
    assert isinstance(worst, float) and worst >= 100.0


def test_nothing_is_reported_before_the_first_sample() -> None:
    reading = LoopLagMonitor().snapshot()
    assert reading["loop_lag_max_ms"] is None and reading["loop_lag_samples"] == 0


async def test_history_is_bounded_and_clear_forgets_the_worst_stall() -> None:
    monitor = LoopLagMonitor(interval_s=0.001, ring=5)
    monitor.start(asyncio.get_running_loop())
    try:
        await _let_it_sample(0.05)
        time.sleep(0.03)
        await _let_it_sample(0.02)
        assert monitor.snapshot()["loop_lag_samples"] == 5
        monitor.clear()
        assert monitor.snapshot()["loop_lag_max_ms"] is None
    finally:
        monitor.stop()


async def test_stop_ends_the_sampling_task() -> None:
    monitor = LoopLagMonitor(interval_s=0.01)
    monitor.start(asyncio.get_running_loop())
    assert monitor.running
    monitor.stop()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not monitor.running
    assert not [task for task in asyncio.all_tasks() if task.get_name() == "loop-lag"]


def test_the_accessor_falls_back_to_a_no_op() -> None:
    uninstall()
    assert isinstance(current_loop_lag(), NullLoopLag)
    assert current_loop_lag().snapshot() == {}


async def test_call_stopped_carries_the_lag_and_the_leak_readings(tmp_path: Path) -> None:
    run = RunContext.create(app_version="9.9.9", channel="dev")
    box = BlackBox(run, tmp_path, queue_size=100, runs_kept=5, max_run_bytes=1_000_000, clip=500)
    perf = PerfLogger(run, tmp_path / "perf", max_bytes=1_000_000, files_kept=2)
    monitor = LoopLagMonitor(interval_s=0.01)
    monitor.start(asyncio.get_running_loop())
    install(perf, box, monitor)
    try:
        await _let_it_sample(0.05)
        lifecycle._record_call("call.stopped")
    finally:
        uninstall()
        monitor.stop()
        box.close()
        perf.close()
    lines = (box.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events: list[dict[str, Any]] = [json.loads(line) for line in lines]
    stopped = next(event for event in events if event["event"] == "call.stopped")
    fields = stopped["fields"]
    assert fields["threads"] >= 1
    assert fields["loop_lag_samples"] > 0 and fields["loop_lag_p95_ms"] is not None
    assert "handles" in fields and "rss_mb" in fields


def test_its_p50_is_the_median_and_its_p95_the_nearest_rank() -> None:
    """The rule every other number uses; it rounded an index, and read 7 and 11 ms here
    (mutation: the old rule)."""
    monitor = LoopLagMonitor()
    monitor._samples.extend(ms / 1000 for ms in range(1, 13))

    snapshot = monitor.snapshot()

    assert (snapshot["loop_lag_p50_ms"], snapshot["loop_lag_p95_ms"]) == (6.5, 12.0)
