"""Local telemetry: the black box, the performance log and the event-loop lag monitor.

Nothing here leaves the machine. The writers live in the telemetry folder of her data
folder (`telemetry.dir`) and can be switched off with `telemetry.enabled`.

Like `logging.getLogger`, they are reached through module-level accessors rather
than passed through every constructor, so a deep call site (the voice loop, the
kernel) can record without new plumbing. The app lifespan installs the real ones and
removes them on shutdown; until then (in tests and scripts) the accessors return
no-op versions.
"""

from __future__ import annotations

from app.telemetry.blackbox import BlackBox, NullBlackBox
from app.telemetry.looplag import LoopLagMonitor, NullLoopLag
from app.telemetry.perf import NullPerfLogger, PerfLogger

_perf: PerfLogger | NullPerfLogger = NullPerfLogger()
_blackbox: BlackBox | NullBlackBox = NullBlackBox()
_loop_lag: LoopLagMonitor | NullLoopLag = NullLoopLag()


def current_perf() -> PerfLogger | NullPerfLogger:
    """The installed performance logger, or a no-op one."""
    return _perf


def current_blackbox() -> BlackBox | NullBlackBox:
    """The installed black box, or a no-op one."""
    return _blackbox


def current_loop_lag() -> LoopLagMonitor | NullLoopLag:
    """The running event-loop lag monitor, or a no-op one."""
    return _loop_lag


def install(perf_logger: PerfLogger, box: BlackBox, loop_lag: LoopLagMonitor | None = None) -> None:
    global _perf, _blackbox, _loop_lag
    _perf, _blackbox = perf_logger, box
    _loop_lag = loop_lag if loop_lag is not None else NullLoopLag()


def uninstall() -> None:
    global _perf, _blackbox, _loop_lag
    _perf, _blackbox, _loop_lag = NullPerfLogger(), NullBlackBox(), NullLoopLag()


__all__ = [
    "BlackBox",
    "LoopLagMonitor",
    "PerfLogger",
    "current_blackbox",
    "current_loop_lag",
    "current_perf",
    "install",
    "uninstall",
]
