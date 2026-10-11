"""Start and stop the telemetry writers for one run of the app (used by the lifespan)."""

from __future__ import annotations

import asyncio
import logging
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.version import APP_CHANNEL, APP_VERSION, APP_VERSION_LABEL
from app.paths import anchored
from app.telemetry import install, uninstall
from app.telemetry.blackbox import BlackBox
from app.telemetry.hooks import CrashHooks, format_traceback
from app.telemetry.looplag import LoopLagMonitor
from app.telemetry.perf import PerfLogger
from app.telemetry.process import rss_mb
from app.telemetry.runctx import RunContext
from app.telemetry.settings import TelemetrySettings

logger = logging.getLogger(__name__)


def telemetry_dir(settings: TelemetrySettings) -> Path:
    """The configured directory, anchored to the installation when relative."""
    return anchored(settings.dir)


@dataclass(slots=True)
class TelemetryRuntime:
    """What one run started, so shutdown can stop exactly that."""

    run: RunContext
    perf: PerfLogger | None = None
    blackbox: BlackBox | None = None
    hooks: CrashHooks | None = None
    loop_lag: LoopLagMonitor | None = None
    previous_run: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return self.blackbox is not None


def start_telemetry(
    settings: TelemetrySettings, loop: asyncio.AbstractEventLoop | None = None
) -> TelemetryRuntime:
    """Open this run's black box and perf log, install the hooks, record `app.start`."""
    run = RunContext.create(app_version=APP_VERSION, channel=APP_CHANNEL)
    if not settings.enabled:
        return TelemetryRuntime(run=run)
    directory = telemetry_dir(settings)
    # Diagnostics are never a reason not to start: a folder that can't be written (closed
    # to this account, a file where it should be, a full disk) leaves this run without
    # them, and the console says so.
    try:
        perf = PerfLogger(
            run,
            directory / "perf",
            max_bytes=settings.perf_max_bytes,
            files_kept=settings.perf_files_kept,
        )
    except OSError as exc:
        logger.warning("diagnostics are off for this run: %s", type(exc).__name__)
        return TelemetryRuntime(run=run)
    try:
        box = BlackBox(
            run,
            directory,
            queue_size=settings.blackbox_queue_size,
            runs_kept=settings.runs_kept,
            max_run_bytes=settings.blackbox_max_run_bytes,
            clip=settings.redaction_clip,
        )
    except OSError as exc:
        perf.close()
        logger.warning("diagnostics are off for this run: %s", type(exc).__name__)
        return TelemetryRuntime(run=run)
    hooks = CrashHooks(box, log_level=logging.getLevelNamesMapping()[settings.capture_log_level])
    hooks.install(loop)
    # Lag is only measurable on a running loop; a script without one simply has none.
    loop_lag: LoopLagMonitor | None = None
    if loop is not None:
        loop_lag = LoopLagMonitor()
        loop_lag.start(loop)
    install(perf, box, loop_lag)
    previous = box.previous_run()
    # No absolute paths and no host name: they would carry the user's account name
    # into a file that is meant to be safe to attach to a bug report.
    box.record(
        "info",
        "app.start",
        version=APP_VERSION,
        label=APP_VERSION_LABEL,
        pid=run.pid,
        python=platform.python_version(),
        os=platform.system(),
        os_release=platform.release(),
        rss_mb=rss_mb(),
    )
    if previous is not None and previous.get("clean_exit") is False:
        box.record(
            "warn",
            "app.previous_run_unclean",
            previous_run_id=previous.get("run_id"),
            previous_version=previous.get("app_version"),
            previous_started_at=previous.get("started_at"),
        )
    return TelemetryRuntime(
        run=run, perf=perf, blackbox=box, hooks=hooks, loop_lag=loop_lag, previous_run=previous
    )


def stop_telemetry(
    runtime: TelemetryRuntime,
    *,
    vram_used_mb: int | None = None,
    startup_error: BaseException | None = None,
) -> None:
    """Record how the run ended, then stop the monitor, remove the hooks, close the writers.

    A run whose start-up failed is recorded with its error and closed as unclean;
    otherwise it would pass for a normal exit, in `run.json` and in the next run's check.
    """
    box, perf, hooks, loop_lag = runtime.blackbox, runtime.perf, runtime.hooks, runtime.loop_lag
    if box is None or perf is None:
        return
    try:
        if startup_error is not None:
            box.record(
                "error",
                "app.startup_failed",
                type=type(startup_error).__name__,
                message=str(startup_error),
                traceback=format_traceback(
                    type(startup_error), startup_error, startup_error.__traceback__
                ),
            )
        box.record(
            "info",
            "app.stop",
            vram_used_mb=vram_used_mb,
            rss_mb=rss_mb(),
            perf_dropped=perf.dropped,
            perf_errors=perf.errors,
            **(loop_lag.snapshot() if loop_lag is not None else {}),
        )
    finally:
        uninstall()
        if loop_lag is not None:
            loop_lag.stop()
        if hooks is not None:
            hooks.uninstall()
        box.close(clean=startup_error is None)
        perf.close()
