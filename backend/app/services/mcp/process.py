"""One tool server's process: started held in a job, read a line at a time, written to
through a queue of its own, stopped.

`subprocess` with threads, not asyncio's subprocesses: on Windows those work only on the
proactor event loop, and uvicorn runs the selector loop under `--reload`, where every
launch failed (bug 14, as `apps.open` found). So one thread reads its output and hands
each line to `on_line`, one keeps the end of what it writes to stderr, and one writes
what is queued for it (`send`, which never blocks). A server that stops reading would
otherwise hold a thread in `write` for good: through the shared thread pool, enough of
them stalled the rest of her backend. Past `BACKLOG` lines waiting, `send` refuses.

A line longer than `max_line` stops the server: a framing it can't keep isn't one to
read on from. What it writes to stderr is kept, its last 64 KiB, for the user to read
(`tail`), never logged: a server may print its own keys.
"""

from __future__ import annotations

import contextlib
import queue
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from io import BufferedReader
from typing import IO, TYPE_CHECKING, cast

from app.services.mcp.setup import Launch

if TYPE_CHECKING:
    from app.services.mcp.job import Job

STDERR_KEPT = 64 * 1024
BACKLOG = 64
_GRACE_S = 2.0
_CLOSE = b""  # queued last: the writer closes its input


class TooLong(Exception):
    """The server sent a line past the limit."""


class Backlog(BrokenPipeError):
    """The server isn't reading what it is sent."""


class ServerProcess:
    """A started server. `on_line` and `on_exit` are called from its reader thread."""

    def __init__(
        self,
        launch: Launch,
        *,
        on_line: Callable[[bytes], None],
        on_exit: Callable[[str], None],
        max_line: int,
    ) -> None:
        self._launch = launch
        self._on_line = on_line
        self._on_exit = on_exit
        self._max_line = max_line
        self._outbox: queue.Queue[bytes] = queue.Queue(maxsize=BACKLOG)
        self._stderr: deque[bytes] = deque()
        self._stderr_size = 0
        self._stopping = False
        self._process: subprocess.Popen[bytes] | None = None
        self._job: Job | None = None

    def start(self) -> None:
        """Start it, held in its job before any of its code runs. Blocks: run on a thread.
        Stopped meanwhile (`stop` before the process existed), it is ended at once."""
        self._launch.folder.mkdir(parents=True, exist_ok=True)
        job: Job | None = None
        flags = 0
        if sys.platform == "win32":
            from app.services.mcp.job import CREATE_SUSPENDED, Job

            job = Job()
            flags = CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                [str(self._launch.program), *self._launch.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._launch.folder,
                env=self._launch.env,
                creationflags=flags,
                start_new_session=sys.platform != "win32",
            )
        except OSError:
            if job is not None:
                job.close()
            raise
        self._process, self._job = process, job
        try:
            if job is not None:
                job.hold(process.pid)
            if self._stopping:
                raise OSError("stopped while it was starting")
        except OSError:
            self._end()
            _close(process)
            raise
        for target, name in ((self._read, "out"), (self._keep_stderr, "err"), (self._write, "in")):
            threading.Thread(target=target, name=f"mcp-{name}", daemon=True).start()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def send(self, line: bytes) -> None:
        """Queue one message's line. Never blocks: `Backlog` when it isn't reading."""
        if self._process is None or self._stopping:
            raise BrokenPipeError("not running")
        try:
            self._outbox.put_nowait(line)
        except queue.Full:
            raise Backlog("it isn't reading") from None

    def stop(self) -> None:
        """Close its input, give it two seconds to leave, then end its whole job. Its
        input is closed by the writer, after what was queued: closing it here would wait
        on a write the server isn't reading."""
        self._stopping = True
        process = self._process
        if process is None:
            return
        with contextlib.suppress(queue.Full):
            self._outbox.put_nowait(_CLOSE)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(_GRACE_S)
        self._end()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(_GRACE_S)
        _close(process)

    def tail(self) -> str:
        """The end of what it wrote to stderr, as text."""
        return b"".join(self._stderr).decode("utf-8", errors="replace")

    def _end(self) -> None:
        job, self._job = self._job, None
        if job is not None:
            job.close()
        process = self._process
        if process is not None and process.poll() is None:
            with contextlib.suppress(OSError):
                process.kill()

    def _write(self) -> None:
        process = self._process
        assert process is not None and process.stdin is not None
        with contextlib.suppress(OSError, ValueError):
            while (line := self._outbox.get()) != _CLOSE:
                process.stdin.write(line)
                process.stdin.flush()
        with contextlib.suppress(OSError, ValueError):
            process.stdin.close()

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        why = "exited"
        try:
            why = self._lines(process.stdout)
        except TooLong:
            why = "line_too_long"
            self._end()
        except (OSError, ValueError):
            why = "exited"
        finally:
            with contextlib.suppress(queue.Full):
                self._outbox.put_nowait(_CLOSE)  # its writer has nothing left to do
            if not self._stopping:
                self._on_exit(why)

    def _lines(self, out: IO[bytes]) -> str:
        while True:
            line = out.readline(self._max_line + 1)
            if not line:
                return "exited"
            if len(line) > self._max_line:
                raise TooLong
            if line.strip():
                self._on_line(line)

    def _keep_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        stream = cast("BufferedReader", process.stderr)  # what Popen gives for a pipe
        with contextlib.suppress(OSError, ValueError):
            for chunk in iter(lambda: stream.read1(4096), b""):
                self._stderr.append(chunk)
                self._stderr_size += len(chunk)
                while self._stderr_size > STDERR_KEPT and len(self._stderr) > 1:
                    self._stderr_size -= len(self._stderr.popleft())


def _close(process: subprocess.Popen[bytes]) -> None:
    """Close the pipes it read from: what was in them is read, or no longer wanted."""
    for stream in (process.stdout, process.stderr):
        with contextlib.suppress(OSError, ValueError):
            if stream is not None:
                stream.close()
