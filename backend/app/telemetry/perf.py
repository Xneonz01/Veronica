"""Performance log: one JSONL line per timed stage, written off the caller's thread.

`log()` only enqueues, so it is safe to call from the live-call path; a single
background thread appends to `perf.jsonl`, rotates it at a size cap and prunes old
files. Stages are free-form names — nothing has to be declared up front.

Only names, numbers and short labels belong in a perf row. Never pass argument
values, file contents or transcripts as metadata: this file is meant to be safe to
attach to a bug report, and unlike the black box it is not redacted.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from app.telemetry.runctx import RunContext

logger = logging.getLogger(__name__)

_STOP = object()  # pushed by close() to wake the writer thread
# Bounded so a stalled disk can't grow the heap without limit. Hitting it means the
# writer thread is stuck, and the losses are counted rather than hidden.
_QUEUE_MAX = 10_000


class PerfLogger:
    """Append-only JSONL writer for stage timings."""

    def __init__(
        self,
        run: RunContext,
        directory: Path,
        *,
        max_bytes: int,
        files_kept: int,
        filename: str = "perf.jsonl",
    ) -> None:
        self._run = run
        self._dir = directory
        self._max_bytes = max_bytes
        self._files_kept = files_kept
        self._path = directory / filename
        self._stem = filename.split(".", 1)[0]
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=_QUEUE_MAX)
        self.errors = 0
        self.dropped = 0
        # A flag as well as the sentinel: close() must never block, and a full queue
        # has no room for the sentinel.
        self._stopping = threading.Event()
        self._closed = False
        self._dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._drain, name="perf-writer", daemon=True)
        self._thread.start()

    @property
    def path(self) -> Path:
        return self._path

    def log(self, stage: str, dur_ms: float, session_id: str | None = None, **meta: Any) -> None:
        """Record one timing. Never blocks and never raises."""
        if self._closed:
            return
        line = {
            "ts": time.time(),
            "run_id": self._run.run_id,
            "app_version": self._run.app_version,
            "stage": stage,
            "dur_ms": round(float(dur_ms), 3),
            "session_id": session_id,
            "meta": meta,
        }
        try:
            self._queue.put_nowait(line)
        except queue.Full:
            self.dropped += 1

    @contextmanager
    def time_stage(self, stage: str, session_id: str | None = None, **meta: Any) -> Iterator[None]:
        """Time a block with `perf_counter`, including when it raises.

        A stage that only reports on success hides the slow failures, which are
        usually the ones worth seeing.
        """
        started = time.perf_counter()
        try:
            yield
        finally:
            self.log(stage, (time.perf_counter() - started) * 1000.0, session_id, **meta)

    def close(self, timeout: float = 2.0) -> None:
        """Stop accepting lines and wait, briefly, for the tail to reach disk."""
        if self._closed:
            return
        self._closed = True
        self._stopping.set()
        with suppress(queue.Full):
            self._queue.put_nowait(_STOP)
        self._thread.join(timeout=timeout)

    # -- writer thread --

    def _drain(self) -> None:
        while True:
            try:
                # Timed rather than blocking forever, so the thread still notices the
                # stop flag when the queue was too full to take the sentinel.
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._stopping.is_set():
                    return
                continue
            if item is _STOP:
                return
            batch = [item]
            # Pull whatever else is queued so a burst costs one file open.
            while True:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _STOP:
                    self._write(batch)
                    return
                batch.append(nxt)
            self._write(batch)

    def _write(self, batch: list[dict[str, Any]]) -> None:
        pending: list[str] = []
        for line in batch:
            try:
                pending.append(json.dumps(line, ensure_ascii=False) + "\n")
            except (TypeError, ValueError):
                # One unserialisable metadata value costs its own line, not the batch.
                self.errors += 1
        try:
            while pending:
                self._rotate_if_needed()
                size = self._path.stat().st_size if self._path.exists() else 0
                # Split the batch at the cap so a large burst can't run past it.
                cut = 0
                for text in pending:
                    cut += 1
                    size += len(text.encode("utf-8"))
                    if size >= self._max_bytes:
                        break
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write("".join(pending[:cut]))
                pending = pending[cut:]
        except Exception:
            # A full disk or a locked file must not kill the writer thread; count the
            # loss and keep going.
            self.errors += len(pending)
            logger.debug("perf write failed for %d line(s)", len(pending), exc_info=True)

    def _rotate_if_needed(self) -> None:
        if not self._path.exists() or self._path.stat().st_size < self._max_bytes:
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = self._dir / f"{self._stem}.{stamp}.jsonl"
        counter = 1
        while target.exists():  # two rotations in the same second
            target = self._dir / f"{self._stem}.{stamp}-{counter}.jsonl"
            counter += 1
        self._path.rename(target)
        self._prune()

    def _prune(self) -> None:
        # By modification time, with the name as a tie-break: rotations within one
        # second share a timestamp and their suffixes don't sort numerically.
        archives = sorted(
            self._dir.glob(f"{self._stem}.*.jsonl"), key=lambda p: (p.stat().st_mtime, p.name)
        )
        for stale in archives[: max(0, len(archives) - self._files_kept)]:
            stale.unlink(missing_ok=True)


class NullPerfLogger:
    """The logger used when none is installed (tests, scripts): does nothing."""

    errors = 0
    dropped = 0

    def log(self, stage: str, dur_ms: float, session_id: str | None = None, **meta: Any) -> None:
        return None

    @contextmanager
    def time_stage(self, stage: str, session_id: str | None = None, **meta: Any) -> Iterator[None]:
        yield

    def close(self, timeout: float = 2.0) -> None:
        return None
