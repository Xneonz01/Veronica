"""Standard-library logging setup for the backend."""

from __future__ import annotations

import logging
import os
import traceback

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# Libraries that log whole payloads at DEBUG: aiosqlite logs every SQL statement with
# its parameters, i.e. memory contents, and websockets every frame of chat and call text
# through uvicorn's error logger, which uvicorn hands it. They stay at INFO even when the
# app runs at DEBUG, so turning on debug logging doesn't copy personal data into the
# console.
QUIET_AT_DEBUG = ("aiosqlite", "uvicorn.error")
# And libraries that log every request's whole address at INFO: httpx a line per request,
# httpcore its connections at DEBUG. With web search (ADR 0003) an address holds what
# she searched for and which page she read, so they speak only at WARNING.
QUIET_URLS = ("httpx", "httpcore")


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger; safe to call more than once (idempotent)."""
    resolved = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(level=resolved, format=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    logging.getLogger().setLevel(resolved)
    quiet_libraries(resolved)


def quiet_libraries(level: int) -> None:
    """Hold the libraries that log personal data above `level` (see above)."""
    for name in QUIET_AT_DEBUG:
        logging.getLogger(name).setLevel(max(level, logging.INFO))
    for name in QUIET_URLS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


def where(exc: BaseException) -> str:
    """An exception's type and the file and line it was raised at, never its message: a
    message can quote what it failed on, an argument, a page or a key (an encoding error
    quotes the whole string), and the console isn't redacted."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return type(exc).__name__
    last = frames[-1]
    return f"{type(exc).__name__} at {os.path.basename(last.filename)}:{last.lineno}"


def log_failure(log: logging.Logger, what: str, exc: BaseException) -> None:
    """Log a failure by its type and place, and at DEBUG its frames, never its message
    (`where`): the frames are source lines, which carry no one's data."""
    log.error("%s: %s", what, where(exc))
    if log.isEnabledFor(logging.DEBUG):
        log.debug("%s, frames:\n%s", what, "".join(traceback.format_tb(exc.__traceback__)))
