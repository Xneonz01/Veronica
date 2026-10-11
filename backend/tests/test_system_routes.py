"""Patch notes and diagnostics over HTTP, and the run folder a real start and stop leaves."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.gpu_manager import CudaGpuModelManager
from app.core.version import APP_VERSION
from app.main import create_app
from app.telemetry import current_blackbox, current_perf
from app.telemetry.blackbox import BlackBox, NullBlackBox
from app.telemetry.perf import NullPerfLogger


def _events(run_dir: Path) -> list[dict[str, Any]]:
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_the_changelog_route_serves_the_patch_notes_newest_first() -> None:
    with TestClient(create_app(), base_url="http://localhost") as client:
        body = client.get("/changelog", params={"limit": 3}).json()
    assert body["current"] == APP_VERSION
    assert len(body["entries"]) == 3
    assert body["entries"][0]["version"] == APP_VERSION
    assert set(body["entries"][0]) == {"version", "label", "date", "channel", "sections"}


def test_diagnostics_describe_the_running_process() -> None:
    app = create_app()
    with TestClient(app, base_url="http://localhost") as client:
        body = client.get("/diagnostics").json()
        run_id = app.state.telemetry.run.run_id
    assert body["telemetry"] is True and body["version"] == APP_VERSION
    assert body["run_id"] == run_id
    assert "loop_lag_samples" in body["loop_lag"]  # the monitor runs with the app
    current = [run for run in body["recent_runs"] if run["run_id"] == run_id]
    assert current and current[0]["clean_exit"] is False  # still running at the time
    assert "app.startup" in body["perf"] or body["perf"] == {}  # may not be flushed yet
    # What each registered model has cost the card: none loaded in the tests.
    assert set(body["gpu_models"]) == {"stt", "tts", "embedding"}
    assert body["gpu_models"]["stt"]["loads"] == 0
    # The runtime's 2 GB estimate, converted once to GiB: 1,907 MiB.
    assert body["gpu_models"]["stt"]["vram_estimate_mb"] == 1907
    # How far behind learning is (mutation: leave it out).
    assert body["learning"] == {"due": 0, "failing": {}, "calls": 0}


def test_her_models_are_released_when_she_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """So the reading `app.stop` takes shows what is left (mutation: don't release)."""
    released: list[str] = []

    async def unload_all(self: CudaGpuModelManager) -> None:
        released.append("all")

    monkeypatch.setattr(CudaGpuModelManager, "unload_all", unload_all)
    with TestClient(create_app(), base_url="http://localhost"):
        assert released == []
    assert released == ["all"]


@pytest.mark.parametrize(
    "target",
    [
        # Opening the database: this failure used to be closed as a clean exit.
        "app.services.memory.runtime.MemoryRuntime.init",
        # Before any store opens: this one used to skip shutdown entirely.
        "app.services.persona.PersonaService.load",
    ],
)
def test_a_failed_start_is_recorded_and_the_run_left_unclean(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(target, fail)
    app = create_app()
    with pytest.raises(RuntimeError, match="locked"), TestClient(app, base_url="http://localhost"):
        pass
    run_dir = app.state.telemetry.blackbox.run_dir
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["clean_exit"] is False and run["ended_at"] is not None
    failed = [event for event in _events(run_dir) if event["event"] == "app.startup_failed"]
    assert failed and failed[0]["fields"]["type"] == "RuntimeError"
    assert "the database is locked" in failed[0]["fields"]["traceback"]
    assert isinstance(current_blackbox(), NullBlackBox)  # nothing left installed


def test_a_clean_start_and_stop_leaves_a_complete_run_folder() -> None:
    app = create_app()
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/health").status_code == 200
        assert isinstance(current_blackbox(), BlackBox)  # installed while running
        box = app.state.telemetry.blackbox
        run_dir = box.run_dir
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["clean_exit"] is True and run["app_version"] == APP_VERSION
    events = _events(run_dir)
    names = [event["event"] for event in events]
    assert names[0] == "app.start" and "app.stop" in names and names[-1] == "blackbox.drops"
    stop = next(event for event in events if event["event"] == "app.stop")
    assert "loop_lag_max_ms" in stop["fields"]
    assert isinstance(current_perf(), NullPerfLogger)  # removed again on shutdown
    perf_file = run_dir.parents[1] / "perf" / "perf.jsonl"
    stages = [json.loads(line)["stage"] for line in perf_file.read_text("utf-8").splitlines()]
    assert "app.startup" in stages
