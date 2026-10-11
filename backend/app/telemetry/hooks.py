"""Feed the black box from the places errors surface on their own.

- `sys.excepthook` and `threading.excepthook`: an exception nothing caught.
- The asyncio loop's exception handler: a task that died with nobody awaiting it.
- `faulthandler`: a native crash (a segfault in a CUDA or audio library), written as
  a plain-text traceback into the run folder, since Python code can't run by then.
- A logging handler that copies WARNING and above into the black box, so the events
  file shows what the console showed just before something went wrong.

Every hook chains to whatever was installed before it, and `uninstall()` puts the
previous hooks back.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import sys
import threading
import traceback
from collections.abc import Callable
from types import TracebackType
from typing import IO, Any

from app.telemetry.blackbox import BlackBox, Level

_TRACEBACK_CHARS = 4000
_OWN_LOGGERS = ("app.telemetry",)  # our own debug lines must not feed back in


def format_traceback(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> str:
    """The traceback as text, keeping its end, which is where the error is."""
    return "".join(traceback.format_exception(exc_type, exc, tb))[-_TRACEBACK_CHARS:]


class BlackBoxLogHandler(logging.Handler):
    """Copies log records at or above its level into the black box."""

    def __init__(self, box: BlackBox, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self._box = box

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(_OWN_LOGGERS):
            return
        try:
            level: Level = "error" if record.levelno >= logging.ERROR else "warn"
            fields: dict[str, Any] = {"logger": record.name, "message": record.getMessage()}
            if record.exc_info and record.exc_info[0] is not None:
                fields["exception"] = record.exc_info[0].__name__
            self._box.record(level, f"log.{record.levelname.lower()}", **fields)
        except Exception:
            self.handleError(record)


class CrashHooks:
    """Installs and removes the process-level hooks for one black box."""

    def __init__(self, box: BlackBox, *, log_level: int = logging.WARNING) -> None:
        self._box = box
        self._log_handler = BlackBoxLogHandler(box, log_level)
        self._prev_excepthook = sys.excepthook
        self._prev_thread_hook = threading.excepthook
        self._prev_loop_handler: Callable[..., Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fault_file: IO[str] | None = None

    def install(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        sys.excepthook = self._on_exception
        threading.excepthook = self._on_thread_exception
        logging.getLogger().addHandler(self._log_handler)
        if loop is not None:
            self._loop = loop
            self._prev_loop_handler = loop.get_exception_handler()
            loop.set_exception_handler(self._on_loop_exception)
        # Leave faulthandler alone if someone else already owns it (pytest does, and so
        # does `python -X faulthandler`); disabling theirs on shutdown would be rude.
        if not faulthandler.is_enabled():
            try:
                self._fault_file = (self._box.run_dir / "faulthandler.log").open(
                    "a", encoding="utf-8"
                )
                faulthandler.enable(file=self._fault_file, all_threads=True)
            except Exception:
                self._close_fault_file()

    def uninstall(self) -> None:
        if sys.excepthook == self._on_exception:
            sys.excepthook = self._prev_excepthook
        if threading.excepthook == self._on_thread_exception:
            threading.excepthook = self._prev_thread_hook
        logging.getLogger().removeHandler(self._log_handler)
        if self._loop is not None and not self._loop.is_closed():
            self._loop.set_exception_handler(self._prev_loop_handler)
        if self._fault_file is not None:
            faulthandler.disable()
            self._close_fault_file()

    def _close_fault_file(self) -> None:
        if self._fault_file is None:
            return
        path = self._box.run_dir / "faulthandler.log"
        self._fault_file.close()
        self._fault_file = None
        try:  # a clean run leaves an empty file behind; don't keep it
            if path.stat().st_size == 0:
                path.unlink()
        except OSError:
            pass

    def _on_exception(
        self, exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            self._box.record(
                "fatal",
                "crash.unhandled_exception",
                type=exc_type.__name__,
                message=str(exc),
                traceback=format_traceback(exc_type, exc, tb),
            )
        self._prev_excepthook(exc_type, exc, tb)

    def _on_thread_exception(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not SystemExit and args.exc_value is not None:
            self._box.record(
                "error",
                "thread.unhandled_exception",
                thread=args.thread.name if args.thread is not None else None,
                type=args.exc_type.__name__,
                message=str(args.exc_value),
                traceback=format_traceback(args.exc_type, args.exc_value, args.exc_traceback),
            )
        self._prev_thread_hook(args)

    def _on_loop_exception(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        fields: dict[str, Any] = {"message": str(context.get("message", ""))}
        if isinstance(exc, BaseException):
            fields["type"] = type(exc).__name__
            fields["traceback"] = format_traceback(type(exc), exc, exc.__traceback__)
        self._box.record("error", "asyncio.unhandled_exception", **fields)
        if self._prev_loop_handler is not None:
            self._prev_loop_handler(loop, context)
        else:
            loop.default_exception_handler(context)
