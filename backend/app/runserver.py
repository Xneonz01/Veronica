"""The launcher: `uv run python -m app.runserver`, from `backend/`.

Three of uvicorn's defaults are wrong for her (read in its source, 0.49.0):

- Its default logging config gives the `uvicorn` loggers handlers of their own and stops
  them reaching the root logger, where the black box listens, so an error uvicorn logs
  (an exception escaping a WebSocket handler) was never recorded. With no logging config
  they reach it.
- Its access log prints every request line, and a memory search puts what was searched
  for in the query string.
- Its graceful shutdown has no time limit: a connection that never closes holds the exit
  for good, and the run is recorded as not closed cleanly.

What `uvicorn.run` does around the server is kept: Ctrl+C ends quietly, and a start that
failed exits with its code 3. Ctrl+Break, how a program that runs her stops a console
process on Windows, ends her as Ctrl+C does. `uv run uvicorn app.main:app` still works,
and the tests serve the app through `TestClient`; this is how she is meant to be started.
"""

from __future__ import annotations

import argparse
import signal
import sys

import uvicorn

from app.core.config import get_settings
from app.core.logging import setup_logging

HOST = "127.0.0.1"  # this computer only
PORT = 8000  # where the app looks for her (`frontend/src/lib/api.ts`)
# The requests still running get this long once she is asked to stop; then uvicorn
# cancels them and still runs her shutdown, so the run is closed cleanly. A document
# still being read is cancelled whole: its row and its passages are written together.
GRACEFUL_SHUTDOWN_S = 10
_STARTUP_FAILURE = 3  # uvicorn's own exit code for a start that failed


def build_config(port: int = PORT) -> uvicorn.Config:
    """The server's settings, apart from `main` so a test can read them without serving."""
    return uvicorn.Config(
        "app.main:app",
        host=HOST,
        port=port,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
    )


def main() -> None:
    """Serve her until she is stopped; exit 3 if she never started."""
    parser = argparse.ArgumentParser(description="Start Veronica's backend on this computer.")
    parser.add_argument("--port", type=_port, default=PORT, help=f"port to listen on ({PORT})")
    options = parser.parse_args()
    # Before the server starts, so its first lines ("Started server process", "Waiting
    # for application startup") reach the console in her format; the black box joins in
    # when her lifespan starts it.
    setup_logging(get_settings().logging.level)
    server = uvicorn.Server(build_config(options.port))
    _break_as_ctrl_c()
    try:
        server.run()
    except KeyboardInterrupt:
        pass  # uvicorn raises Ctrl+C again after its own shutdown, as its run() expects
    if not server.started:
        sys.exit(_STARTUP_FAILURE)


def _break_as_ctrl_c() -> None:
    """uvicorn raises Ctrl+Break again after its shutdown, as it does Ctrl+C, and
    Windows' default for it ends the process with 3, the code of a start that failed,
    after a clean stop."""
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, signal.default_int_handler)


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{value} is not a port (1 to 65535)")
    return port


if __name__ == "__main__":
    main()
