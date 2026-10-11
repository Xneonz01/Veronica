"""The launcher: this computer only, no access log, uvicorn's records reaching the root
logger (and so the black box) without the text of chats and calls at DEBUG, a bounded
shutdown, a quiet Ctrl+C (and Ctrl+Break) and uvicorn's exit code for a start that
failed."""

from __future__ import annotations

import logging
import signal
import sys
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn

from app import runserver
from app.core.logging import setup_logging

_UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")


@pytest.fixture(autouse=True)
def _logging_left_as_found() -> Iterator[None]:
    """uvicorn configures its loggers when a config is built; the tests after these
    mustn't inherit what they did."""
    root = logging.getLogger()
    saved = {}
    for name in _UVICORN:
        logger = logging.getLogger(name)
        saved[name] = (logger.level, logger.propagate, list(logger.handlers))
    root_level = root.level
    yield
    root.setLevel(root_level)
    for name, (level, propagate, handlers) in saved.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = propagate
        logger.handlers = handlers


def test_she_listens_on_this_computer_only_with_a_bounded_shutdown() -> None:
    config = runserver.build_config()
    assert (config.host, config.port) == ("127.0.0.1", 8000)
    assert config.access_log is False
    assert config.log_config is None
    assert config.timeout_graceful_shutdown == 10
    assert runserver.build_config(8765).port == 8765


def test_uvicorn_records_reach_the_root_logger_and_requests_are_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the black box's root handler sees once uvicorn has set up its logging."""
    runserver.build_config()  # uvicorn applies its logging settings when it is configured
    with caplog.at_level(logging.INFO):
        logging.getLogger("uvicorn.error").error("Exception in ASGI application")
        logging.getLogger("uvicorn.access").info('127.0.0.1 - "GET /memory?q=secret" 200')
    messages = [record.getMessage() for record in caplog.records]
    assert "Exception in ASGI application" in messages
    assert not any("secret" in message for message in messages)


def test_debug_logging_leaves_the_text_of_chats_and_calls_out() -> None:
    """websockets logs every frame through uvicorn's error logger at DEBUG."""
    setup_logging("DEBUG")
    runserver.build_config()
    assert not logging.getLogger("uvicorn.error").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("uvicorn.error").isEnabledFor(logging.WARNING)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """`main` with its server faked: `started` and what `run` does are the test's. The
    Ctrl+Break handler `main` installs is put back after, so no test inherits it."""
    monkeypatch.setattr(runserver, "setup_logging", lambda _level: None)
    monkeypatch.setattr("sys.argv", ["runserver"])
    found = signal.getsignal(signal.SIGBREAK) if sys.platform == "win32" else None

    def install(started: bool, stopped_by: type[BaseException] | None) -> None:
        def run(self: uvicorn.Server, sockets: Any = None) -> None:
            self.started = started
            if stopped_by is not None:
                raise stopped_by

        monkeypatch.setattr(uvicorn.Server, "run", run)

    yield install
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, found)


def test_ctrl_c_ends_her_quietly(served: Any) -> None:
    served(started=True, stopped_by=KeyboardInterrupt)
    runserver.main()  # no traceback, no exit code but 0


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl+Break is Windows' own")
def test_ctrl_break_ends_her_as_ctrl_c_does(served: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """uvicorn raises it again after a clean stop, into the handler it found; Windows'
    own ends the process with 3, a failed start's code (mutation: leave it)."""

    def run(self: uvicorn.Server, sockets: Any = None) -> None:
        self.started = True
        handler = signal.getsignal(signal.SIGBREAK)
        if not callable(handler):
            raise SystemExit(3)  # what Windows' default does, without ending the tests
        handler(signal.SIGBREAK, None)

    monkeypatch.setattr(uvicorn.Server, "run", run)
    runserver.main()  # no exit code but 0


def test_a_start_that_failed_exits_with_uvicorns_code(served: Any) -> None:
    served(started=False, stopped_by=None)
    with pytest.raises(SystemExit) as exit:
        runserver.main()
    assert exit.value.code == 3


@pytest.mark.parametrize("port", ["0", "65536", "-1"])
def test_a_port_that_isnt_one_is_refused_before_she_starts(
    served: Any, monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    served(started=True, stopped_by=None)
    monkeypatch.setattr("sys.argv", ["runserver", "--port", port])
    with pytest.raises(SystemExit) as exit:
        runserver.main()
    assert exit.value.code == 2  # argparse's own
