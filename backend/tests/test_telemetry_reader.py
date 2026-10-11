"""Reading the black box and the perf log back, including half-written files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.telemetry.blackbox import BlackBox
from app.telemetry.perf import PerfLogger
from app.telemetry.reader import latest_crash, list_runs, perf_summary, recent_events
from app.telemetry.runctx import RunContext


def _run() -> RunContext:
    return RunContext.create(app_version="9.9.9", channel="dev")


def test_runs_are_listed_newest_first_with_their_exit_state(tmp_path: Path) -> None:
    crashed = BlackBox(_run(), tmp_path, queue_size=10, runs_kept=5, max_run_bytes=10_000, clip=100)
    clean = BlackBox(_run(), tmp_path, queue_size=10, runs_kept=5, max_run_bytes=10_000, clip=100)
    clean.close()
    runs = list_runs(tmp_path)
    by_id = {run["run_id"]: run for run in runs}
    assert by_id[crashed.run_dir.name]["clean_exit"] is False
    assert by_id[clean.run_dir.name]["clean_exit"] is True
    crashed.close()


def test_recent_events_filters_by_level(tmp_path: Path) -> None:
    box = BlackBox(_run(), tmp_path, queue_size=10, runs_kept=5, max_run_bytes=10_000, clip=100)
    box.record("info", "app.start")
    box.record("error", "log.error", message="bad")
    box.close()
    events = recent_events(tmp_path, box.run_dir.name, min_level="warn")
    assert [event["event"] for event in events] == ["log.error"]


def test_latest_crash_is_none_until_something_fatal_happens(tmp_path: Path) -> None:
    assert latest_crash(tmp_path) is None
    box = BlackBox(_run(), tmp_path, queue_size=10, runs_kept=5, max_run_bytes=10_000, clip=100)
    box.record("fatal", "crash.unhandled_exception", type="X")
    box.close()
    crash = latest_crash(tmp_path)
    assert crash is not None and crash["event"] == "crash.unhandled_exception"


def test_perf_summary_skips_interrupted_and_tool_turns_and_torn_lines(tmp_path: Path) -> None:
    run = _run()
    logger = PerfLogger(run, tmp_path, max_bytes=1_000_000, files_kept=2)
    for value in (100.0, 200.0, 300.0):
        logger.log("voice.ttft", value, tool_calls=0)
    logger.log("voice.ttft", 9000.0, barged_in=True)
    logger.log("voice.ttft", 8000.0, tool_calls=1)  # waited on a tool and a spoken "yes"
    logger.close()
    with (tmp_path / "perf.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"stage": "voice.ttft", "dur_ms": ')  # a line still being written
    summary = perf_summary(tmp_path / "perf.jsonl")
    assert summary["voice.ttft"] == {"n": 3, "p50": 200.0, "p95": 300.0, "max": 300.0}
    assert perf_summary(tmp_path / "perf.jsonl", run_ids=["other"]) == {}


@pytest.mark.parametrize(
    ("count", "p95"), [(1, 1.0), (19, 19.0), (20, 19.0), (60, 57.0), (100, 95.0)]
)
def test_the_p95_is_the_nearest_rank(tmp_path: Path, count: int, p95: float) -> None:
    """Bug 26: with 20 turns it reported the slowest one, and one rank too high at 60 and
    100 (mutation: `round(0.95 * n + 0.5)`)."""
    logger = PerfLogger(_run(), tmp_path, max_bytes=1_000_000, files_kept=2)
    for value in range(1, count + 1):
        logger.log("voice.ttft", float(value), tool_calls=0)
    logger.close()

    assert perf_summary(tmp_path / "perf.jsonl")["voice.ttft"]["p95"] == p95


def test_missing_files_read_as_empty(tmp_path: Path) -> None:
    assert list_runs(tmp_path) == []
    assert recent_events(tmp_path, "nope") == []
    assert perf_summary(tmp_path / "perf.jsonl") == {}
    assert json.dumps(perf_summary(tmp_path / "missing.jsonl")) == "{}"


def test_only_a_non_empty_native_crash_log_counts(tmp_path: Path) -> None:
    box = BlackBox(_run(), tmp_path, queue_size=10, runs_kept=5, max_run_bytes=10_000, clip=100)
    crash_log = box.run_dir / "faulthandler.log"
    crash_log.write_text("", encoding="utf-8")  # what a running process leaves open
    assert list_runs(tmp_path)[0]["native_crash_log"] is False
    crash_log.write_text("Fatal Python error: Segmentation fault", encoding="utf-8")
    assert list_runs(tmp_path)[0]["native_crash_log"] is True
    box.close()
