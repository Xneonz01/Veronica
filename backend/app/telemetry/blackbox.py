"""The black box: a per-run flight recorder for crashes, errors and lifecycle events.

Every run gets a folder under `<telemetry dir>/runs/<run_id>/`:

    run.json       identity + `clean_exit`, written false at start and true only by a
                   clean `close()`; false with no `ended_at` means the process died,
                   false with an `ended_at` means it shut down after failing to start
    events.jsonl   one redacted JSON object per event

Fatal events skip the queue and are written synchronously, to `events.jsonl` and to
`<telemetry dir>/latest_crash.json`, because a dying process may never schedule the
writer thread again. Under pressure, warn and error events may evict a queued
debug/info event; debug and info are dropped and counted. Every loss is reported in
a final `blackbox.drops` event. Redaction happens here, on the write path, so a
caller can't forget it.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from app.telemetry.redact import redact_value
from app.telemetry.runctx import RunContext

logger = logging.getLogger(__name__)

Level = Literal["debug", "info", "warn", "error", "fatal"]
LEVELS: tuple[Level, ...] = ("debug", "info", "warn", "error", "fatal")
_EVICTABLE = frozenset({"debug", "info"})


class BlackBox:
    """Bounded, non-blocking event recorder with a synchronous path for fatal events."""

    def __init__(
        self,
        run: RunContext,
        directory: Path,
        *,
        queue_size: int,
        runs_kept: int,
        max_run_bytes: int,
        clip: int,
    ) -> None:
        self._run = run
        self._dir = directory
        self._runs_dir = directory / "runs"
        self._run_dir = self._runs_dir / run.run_id
        self._events_path = self._run_dir / "events.jsonl"
        self._run_json_path = self._run_dir / "run.json"
        self._crash_path = directory / "latest_crash.json"
        self._max_queue = queue_size
        self._max_bytes = max_run_bytes
        self._clip = clip
        self._written = 0

        self._buf: deque[dict[str, Any]] = deque()
        self._cond = threading.Condition()
        self._io_lock = threading.Lock()
        self._stopping = False
        self._closed = False
        self.errors = 0
        self.capped = 0  # events refused because this run's file reached its size cap
        self.dropped: dict[str, int] = dict.fromkeys(LEVELS, 0)

        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._write_run_json(clean_exit=False)
        self._prune_runs(runs_kept)
        self._thread = threading.Thread(target=self._drain, name="blackbox-writer", daemon=True)
        self._thread.start()

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def directory(self) -> Path:
        return self._dir

    def record(self, level: Level, event: str, **fields: Any) -> None:
        """Record one event. Never blocks on disk and never raises into the caller."""
        try:
            if self._closed and level != "fatal":
                return
            entry = self._entry(level, event, fields)
            if level == "fatal":
                self._record_fatal(entry)
                return
            with self._cond:
                if len(self._buf) < self._max_queue or (
                    level in ("warn", "error") and self._evict_low_priority()
                ):
                    self._buf.append(entry)
                    self._cond.notify()
                    return
                self.dropped[level] += 1
        except Exception:
            # A recorder that can crash its caller is worse than no recorder.
            self.errors += 1
            logger.debug("blackbox record failed for %r", event, exc_info=True)

    def previous_run(self) -> dict[str, Any] | None:
        """`run.json` of the most recent earlier run, if one exists and is readable."""
        try:
            earlier = sorted(
                p for p in self._runs_dir.iterdir() if p.is_dir() and p != self._run_dir
            )
        except OSError:
            return None
        for folder in reversed(earlier):
            try:
                data = json.loads((folder / "run.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                return data
        return None

    def close(self, timeout: float = 2.0, *, clean: bool = True) -> None:
        """Flush, append the loss counters, and record how the run ended.

        `clean=False` is for a run that stopped in an orderly way but didn't work (its
        start-up failed): it gets an end time but stays marked unclean.
        """
        if self._closed:
            return
        self._closed = True
        try:
            with self._cond:
                self._stopping = True
                self._cond.notify_all()
            self._thread.join(timeout=timeout)
            with self._cond:  # anything the writer thread didn't reach
                leftover = list(self._buf)
                self._buf.clear()
            if leftover:
                self._flush(leftover)
            counts = {**self.dropped, "errors": self.errors, "capped": self.capped}
            self._flush([self._entry("info", "blackbox.drops", counts)], force=True)
            self._write_run_json(clean_exit=clean, ended=True)
        except Exception:
            # Tidying telemetry must never stop the app from exiting.
            self.errors += 1
            logger.debug("blackbox close failed", exc_info=True)

    # -- internals --

    def _entry(self, level: Level, event: str, fields: dict[str, Any]) -> dict[str, Any]:
        return {
            "ts": time.time(),
            "run_id": self._run.run_id,
            "app_version": self._run.app_version,
            "level": level,
            "event": event,
            "fields": fields,
        }

    def _evict_low_priority(self) -> bool:
        """Drop the oldest debug/info entry. The caller holds the condition lock."""
        for index, queued in enumerate(self._buf):
            if queued["level"] in _EVICTABLE:
                del self._buf[index]
                self.dropped[queued["level"]] += 1
                return True
        return False

    def _record_fatal(self, entry: dict[str, Any]) -> None:
        self._flush([entry], force=True)
        try:
            payload = {
                "run_id": self._run.run_id,
                "app_version": self._run.app_version,
                "ts": entry["ts"],
                "event": entry["event"],
                "fields": redact_value(entry["fields"], self._clip),
            }
            self._crash_path.parent.mkdir(parents=True, exist_ok=True)
            self._crash_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            self.errors += 1
            logger.debug("latest_crash write failed", exc_info=True)

    def _drain(self) -> None:
        while True:
            with self._cond:
                while not self._buf and not self._stopping:
                    self._cond.wait()
                if not self._buf and self._stopping:
                    return
                batch = list(self._buf)
                self._buf.clear()
            self._flush(batch)

    def _flush(self, batch: list[dict[str, Any]], *, force: bool = False) -> None:
        """Append redacted lines. `force` writes past the size cap (fatal, drops)."""
        try:
            with self._io_lock:
                lines: list[str] = []
                for entry in batch:
                    line = {**entry, "fields": redact_value(entry["fields"], self._clip)}
                    text = json.dumps(line, ensure_ascii=False, default=str) + "\n"
                    size = len(text.encode("utf-8"))
                    if not force and self._written + size > self._max_bytes:
                        self.capped += 1
                        continue
                    self._written += size
                    lines.append(text)
                if not lines:
                    return
                self._run_dir.mkdir(parents=True, exist_ok=True)
                with self._events_path.open("a", encoding="utf-8") as handle:
                    handle.write("".join(lines))
        except Exception:
            # Losing a batch is survivable; losing the writer thread is not.
            self.errors += len(batch)
            logger.debug("blackbox flush failed for %d event(s)", len(batch), exc_info=True)

    def _write_run_json(self, *, clean_exit: bool, ended: bool = False) -> None:
        payload = {
            "run_id": self._run.run_id,
            "app_version": self._run.app_version,
            "channel": self._run.channel,
            "pid": self._run.pid,
            "started_at": self._run.started_at,
            "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S%z") if ended else None,
            "clean_exit": clean_exit,
        }
        try:
            self._run_json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            # An unwritable run folder must not stop the app from starting.
            self.errors += 1
            logger.debug("run.json write failed", exc_info=True)

    def _prune_runs(self, keep: int) -> None:
        """Delete the oldest run folders beyond `keep` (names sort chronologically)."""
        try:
            folders = sorted(p for p in self._runs_dir.iterdir() if p.is_dir())
            for stale in folders[: max(0, len(folders) - keep)]:
                if stale != self._run_dir:  # never the folder this run writes into
                    shutil.rmtree(stale, ignore_errors=True)
        except Exception:
            self.errors += 1
            logger.debug("run retention prune failed", exc_info=True)


class NullBlackBox:
    """The recorder used when none is installed (tests, scripts): does nothing."""

    errors = 0
    capped = 0

    def record(self, level: Level, event: str, **fields: Any) -> None:
        return None

    def previous_run(self) -> dict[str, Any] | None:
        return None

    def close(self, timeout: float = 2.0, *, clean: bool = True) -> None:
        return None
