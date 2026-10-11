"""Standard-library logging setup for the backend."""

from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# Libraries that log whole payloads at DEBUG: aiosqlite logs every SQL statement with
# its parameters, i.e. memory contents, and websockets every frame of chat and call text
# through uvicorn's error logger, which uvicorn hands it. They stay at INFO even when the
# app runs at DEBUG, so turning on debug logging doesn't copy personal data into the
# console.
QUIET_AT_DEBUG = ("aiosqlite", "uvicorn.error")


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger; safe to call more than once (idempotent)."""
    resolved = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(level=resolved, format=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    logging.getLogger().setLevel(resolved)
    for name in QUIET_AT_DEBUG:
        logging.getLogger(name).setLevel(max(resolved, logging.INFO))
