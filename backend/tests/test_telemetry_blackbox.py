"""The black box: run folders, clean-exit marking, fatal events, limits and retention."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.telemetry.blackbox import BlackBox, NullBlackBox
from app.telemetry.redact import PLACEHOLDER
from app.telemetry.runctx import RunContext


def _box(directory: Path, **overrides: Any) -> BlackBox:
    options: dict[str, Any] = {
        "queue_size": 100,
        "runs_kept": 5,
        "max_run_bytes": 1_000_000,
        "clip": 500,
    }
    options.update(overrides)
    run = RunContext.create(app_version="9.9.9", channel="dev")
    return BlackBox(run, directory, **options)


def _events(box: BlackBox) -> list[dict[str, Any]]:
    path = box.run_dir / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _run_json(box: BlackBox) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((box.run_dir / "run.json").read_text(encoding="utf-8"))
    return data


def test_a_run_starts_unclean_and_close_marks_it_clean(tmp_path: Path) -> None:
    box = _box(tmp_path)
    assert _run_json(box)["clean_exit"] is False
    box.record("info", "app.start", version="9.9.9")
    box.close()
    assert _run_json(box)["clean_exit"] is True
    names = [event["event"] for event in _events(box)]
    assert names == ["app.start", "blackbox.drops"]


def test_an_orderly_close_after_a_failed_start_stays_unclean(tmp_path: Path) -> None:
    box = _box(tmp_path)
    assert _run_json(box)["ended_at"] is None  # still running, or died
    box.close(clean=False)
    run = _run_json(box)
    assert run["clean_exit"] is False and run["ended_at"] is not None


def test_fields_are_redacted_on_the_way_to_disk(tmp_path: Path) -> None:
    box = _box(tmp_path)
    box.record("warn", "log.warning", message="password=hunter2", api_key="abc")
    box.close()
    fields = _events(box)[0]["fields"]
    assert "hunter2" not in json.dumps(fields)
    assert fields["api_key"] == PLACEHOLDER


def test_a_fatal_event_is_written_at_once_and_kept_as_the_latest_crash(tmp_path: Path) -> None:
    box = _box(tmp_path)
    box.record("fatal", "crash.unhandled_exception", type="RuntimeError", message="boom")
    # Written synchronously: present before close() and before the writer runs.
    assert any(e["event"] == "crash.unhandled_exception" for e in _events(box))
    crash = json.loads((tmp_path / "latest_crash.json").read_text(encoding="utf-8"))
    assert crash["event"] == "crash.unhandled_exception"
    assert crash["fields"]["type"] == "RuntimeError"
    box.close()


def test_the_previous_unclean_run_is_found(tmp_path: Path) -> None:
    first = _box(tmp_path)  # never closed: looks like a crash
    second = _box(tmp_path)
    previous = second.previous_run()
    assert previous is not None
    assert previous["run_id"] == first._run.run_id and previous["clean_exit"] is False
    first.close()
    second.close()


def test_old_runs_are_pruned_but_never_the_current_one(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    for index in range(6):
        (runs / f"20200101_00000{index}_aaaaaa").mkdir(parents=True)
    box = _box(tmp_path, runs_kept=3)
    remaining = sorted(p.name for p in runs.iterdir())
    assert len(remaining) == 3
    assert box.run_dir.name in remaining
    box.close()


def test_a_full_queue_lets_warnings_evict_info(tmp_path: Path) -> None:
    box = _box(tmp_path, queue_size=2)
    with box._cond:  # hold the lock so the writer can't drain while we fill the queue
        box._buf.extend(
            [box._entry("info", "a", {}), box._entry("info", "b", {})],
        )
    box.record("warn", "important")
    box.record("info", "dropped")
    assert box.dropped["info"] >= 1
    box.close()
    names = [event["event"] for event in _events(box)]
    assert "important" in names


def test_the_per_run_size_cap_stops_ordinary_events_but_not_fatal_ones(tmp_path: Path) -> None:
    box = _box(tmp_path, max_run_bytes=400)
    for index in range(20):
        box.record("info", "chatter", index=index, padding="x" * 50)
    box.close()
    box.record("fatal", "crash.after_close", type="X")  # fatal still lands after close
    names = [event["event"] for event in _events(box)]
    assert box.capped > 0
    assert names[-1] == "crash.after_close"
    assert "blackbox.drops" in names


def test_record_never_raises_into_the_caller(tmp_path: Path) -> None:
    box = _box(tmp_path)
    box.record("info", "odd", value=object(), nested={"a": [object()]})
    box.close()
    assert _events(box)[0]["event"] == "odd"


def test_the_null_black_box_does_nothing() -> None:
    null = NullBlackBox()
    null.record("fatal", "x", a=1)
    assert null.previous_run() is None
    null.close()
