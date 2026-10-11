"""The performance log: schema, flushing, rotation, and never blocking the caller."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.telemetry import perf as perf_module
from app.telemetry.perf import NullPerfLogger, PerfLogger
from app.telemetry.runctx import RunContext


def _run() -> RunContext:
    return RunContext.create(app_version="9.9.9", channel="dev")


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_line_carries_the_run_identity_and_metadata(tmp_path: Path) -> None:
    run = _run()
    logger = PerfLogger(run, tmp_path, max_bytes=1_000_000, files_kept=2)
    logger.log("chat.turn", 12.34567, "s1", tools=2)
    logger.close()
    [line] = _lines(tmp_path / "perf.jsonl")
    assert line["run_id"] == run.run_id and line["app_version"] == "9.9.9"
    assert line["stage"] == "chat.turn" and line["dur_ms"] == 12.346
    assert line["session_id"] == "s1" and line["meta"] == {"tools": 2}


def test_close_flushes_everything_queued(tmp_path: Path) -> None:
    logger = PerfLogger(_run(), tmp_path, max_bytes=10_000_000, files_kept=2)
    for index in range(500):
        logger.log("stage", float(index))
    logger.close()
    assert len(_lines(tmp_path / "perf.jsonl")) == 500


def test_time_stage_reports_a_block_that_raised(tmp_path: Path) -> None:
    logger = PerfLogger(_run(), tmp_path, max_bytes=1_000_000, files_kept=2)
    with pytest.raises(ValueError), logger.time_stage("slow.fail"):
        time.sleep(0.01)
        raise ValueError("boom")
    logger.close()
    [line] = _lines(tmp_path / "perf.jsonl")
    assert line["stage"] == "slow.fail" and float(str(line["dur_ms"])) >= 5.0


def test_the_file_rotates_at_the_cap_and_old_archives_are_pruned(tmp_path: Path) -> None:
    logger = PerfLogger(_run(), tmp_path, max_bytes=300, files_kept=2)
    for _ in range(40):
        logger.log("stage", 1.0)
        time.sleep(0.002)  # let the writer take lines one batch at a time
    logger.close()
    archives = list(tmp_path.glob("perf.*.jsonl"))
    assert (tmp_path / "perf.jsonl").exists()
    assert 1 <= len(archives) <= 2


def test_a_full_queue_drops_and_counts_instead_of_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perf_module, "_QUEUE_MAX", 5)
    logger = PerfLogger(_run(), tmp_path, max_bytes=1_000_000, files_kept=2)
    logger._stopping.set()  # stop the writer so the queue fills up
    logger._thread.join(timeout=1)
    for _ in range(8):
        logger.log("stage", 1.0)
    assert logger.dropped == 3
    started = time.perf_counter()
    logger.close()
    assert time.perf_counter() - started < 3


def test_logging_after_close_is_ignored(tmp_path: Path) -> None:
    logger = PerfLogger(_run(), tmp_path, max_bytes=1_000_000, files_kept=2)
    logger.close()
    logger.log("late", 1.0)
    assert not (tmp_path / "perf.jsonl").exists()


def test_the_null_logger_accepts_everything() -> None:
    null = NullPerfLogger()
    null.log("x", 1.0, "s", a=1)
    with null.time_stage("y"):
        pass
    null.close()
