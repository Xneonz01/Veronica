"""The kernel's timings: one `kernel.invoke` perf row per request, and its duration.

Names, decisions and outcomes only, never arguments (`tests/test_telemetry_canary.py`).
"""

from __future__ import annotations

import time

from app.kernel.capability import Decision
from app.telemetry import current_perf


def ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def measure(name: str, decision: Decision, *, ok: bool, started: float) -> None:
    """One `kernel.invoke` perf row: the capability, the decision, the outcome — no args."""
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    current_perf().log("kernel.invoke", elapsed_ms, capability=name, decision=decision.value, ok=ok)
