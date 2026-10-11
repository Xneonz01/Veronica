"""The running version, the patch notes, a diagnostics summary for bug reports, and the
check of this computer."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from app import doctor
from app.core.changelog import ChangelogError, load_changelog
from app.core.version import APP_CHANNEL, APP_VERSION, APP_VERSION_LABEL
from app.services.chat.learning_queue import LearningQueue
from app.telemetry import current_loop_lag
from app.telemetry.reader import latest_crash, list_runs, perf_summary, recent_events
from app.telemetry.runtime import TelemetryRuntime, telemetry_dir

if TYPE_CHECKING:
    from app.services.memory.runtime import MemoryRuntime
    from app.services.voice.runtime import VoiceRuntime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

_RUN_FIELDS = ("run_id", "app_version", "started_at", "ended_at", "clean_exit", "native_crash_log")


@router.get("/changelog")
async def changelog(limit: Annotated[int, Query(ge=1, le=100)] = 50) -> dict[str, Any]:
    """The patch notes, newest first."""
    try:
        entries = await asyncio.to_thread(load_changelog)
    except ChangelogError as exc:
        logger.warning("patch notes unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail="The patch notes aren't available right now."
        ) from exc
    return {"current": APP_VERSION, "entries": [entry.as_dict() for entry in entries[:limit]]}


@router.get("/diagnostics")
async def diagnostics(request: Request) -> dict[str, Any]:
    """Version, recent runs, the last crash and pipeline timings, read from local files,
    what each model has cost the card so far (`core/gpu_ledger.py`), and how far behind
    learning is (`chat/learning_queue.py`, `memory/call_queue.py`)."""
    identity = {
        "version": APP_VERSION,
        "label": getattr(request.app.state, "version_label", APP_VERSION_LABEL),
        "channel": APP_CHANNEL,
        "gpu_models": _model_costs(getattr(request.app.state, "voice_runtime", None)),
        "learning": await _learning(getattr(request.app.state, "memory_runtime", None)),
    }
    telemetry: TelemetryRuntime | None = getattr(request.app.state, "telemetry", None)
    if telemetry is None or not telemetry.enabled:
        return {**identity, "telemetry": False}
    directory = telemetry_dir(request.app.state.settings.telemetry)
    summary = await asyncio.to_thread(_summary, directory, telemetry)
    # Live, unlike the rest: whether the loop is being held up right now.
    return {**identity, "telemetry": True, "loop_lag": current_loop_lag().snapshot(), **summary}


@router.get("/diagnostics/doctor")
async def doctor_checks(request: Request) -> dict[str, Any]:
    """This computer against what Veronica was measured using (`app/doctor.py`), for
    "This computer" in Settings. It only looks: nothing is created, loaded or downloaded,
    and however often it is asked, the computer is examined at most once in two seconds."""
    runner: doctor.Doctor = request.app.state.doctor
    checks = await runner.checks(request.app.state.settings)
    return {"checks": [check.model_dump() for check in checks]}


async def _learning(memory: MemoryRuntime | None) -> dict[str, Any] | None:
    """The conversations and finished calls learning hasn't read yet; None when she
    started without memory, or a queue couldn't be read: the rest of the page still
    stands."""
    if memory is None:
        return None
    try:
        backlog = (await LearningQueue(memory.store).backlog())._asdict()
        return {**backlog, "calls": await memory.calls.waiting()}
    except Exception as exc:
        logger.warning("the learning backlog couldn't be read (%s)", type(exc).__name__)
        return None


def _model_costs(runtime: VoiceRuntime | None) -> dict[str, Any]:
    """What each model has cost the card so far; nothing before the runtime is built."""
    if runtime is None:
        return {}
    return {model: cost.model_dump() for model, cost in runtime.manager.ledger().items()}


def _summary(directory: Path, telemetry: TelemetryRuntime) -> dict[str, Any]:
    previous = telemetry.previous_run
    return {
        "run_id": telemetry.run.run_id,
        "previous_run": (
            {field: previous.get(field) for field in _RUN_FIELDS if field in previous}
            if previous
            else None
        ),
        "recent_runs": [
            {field: run.get(field) for field in _RUN_FIELDS} for run in list_runs(directory)
        ],
        "last_crash": latest_crash(directory),
        "recent_problems": recent_events(
            directory, telemetry.run.run_id, min_level="warn", limit=20
        ),
        "perf": perf_summary(directory / "perf" / "perf.jsonl"),
    }
