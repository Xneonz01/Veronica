"""Read the black box and the performance log back: for the CLI and `/diagnostics`.

Everything here tolerates a half-written last line and missing files, because the
writers may be appending while it reads.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from app.telemetry.percentile import nearest_rank

_LEVEL_ORDER = {"debug": 0, "info": 1, "warn": 2, "error": 3, "fatal": 4}
_TAIL_BYTES = 4_000_000  # perf summaries read at most the last ~4 MB of the log


def _json_lines(path: Path, *, tail_bytes: int | None = None) -> Iterator[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            if tail_bytes is not None:
                size = handle.seek(0, 2)
                handle.seek(max(0, size - tail_bytes))
                if size > tail_bytes:
                    handle.readline()  # skip the partial line we landed in
            data = handle.read()
    except OSError:
        return
    for raw in data.decode("utf-8", errors="replace").splitlines():
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            yield item


def list_runs(directory: Path, limit: int = 10) -> list[dict[str, Any]]:
    """`run.json` of the newest runs, newest first."""
    runs_dir = directory / "runs"
    try:
        folders = sorted((p for p in runs_dir.iterdir() if p.is_dir()), reverse=True)
    except OSError:
        return []
    runs: list[dict[str, Any]] = []
    for folder in folders[:limit]:
        try:
            data = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"run_id": folder.name, "clean_exit": None}
        if isinstance(data, dict):
            data["native_crash_log"] = _has_content(folder / "faulthandler.log")
            runs.append(data)
    return runs


def _has_content(path: Path) -> bool:
    """A running process keeps an empty crash log open, so only a non-empty one counts."""
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def recent_events(
    directory: Path, run_id: str, *, min_level: str = "warn", limit: int = 50
) -> list[dict[str, Any]]:
    """The last `limit` events of one run at or above `min_level`."""
    floor = _LEVEL_ORDER.get(min_level, 2)
    events = [
        event
        for event in _json_lines(directory / "runs" / run_id / "events.jsonl")
        if _LEVEL_ORDER.get(str(event.get("level")), 0) >= floor
    ]
    return events[-limit:]


def latest_crash(directory: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((directory / "latest_crash.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def perf_summary(
    perf_path: Path, *, run_ids: Iterable[str] | None = None
) -> dict[str, dict[str, float | int]]:
    """Per stage: count, p50, p95 and max of `dur_ms` over the recent log.

    Voice turns that were interrupted or failed are left out: their durations
    measure how long until the turn stopped, not how long a turn takes. So are voice
    turns that ran a tool, whose timings include the tool and any spoken
    confirmation.
    """
    wanted = set(run_ids) if run_ids is not None else None
    samples: dict[str, list[float]] = {}
    for row in _json_lines(perf_path, tail_bytes=_TAIL_BYTES):
        if wanted is not None and row.get("run_id") not in wanted:
            continue
        meta = row.get("meta") or {}
        if isinstance(meta, dict) and (
            meta.get("barged_in") or meta.get("failed") or meta.get("tool_calls")
        ):
            continue
        stage, value = row.get("stage"), row.get("dur_ms")
        if isinstance(stage, str) and isinstance(value, (int, float)):
            samples.setdefault(stage, []).append(float(value))
    return {stage: _stats(values) for stage, values in sorted(samples.items())}


def _stats(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": round(statistics.median(ordered), 1),
        "p95": round(nearest_rank(ordered, 0.95), 1),
        "max": round(ordered[-1], 1),
    }
