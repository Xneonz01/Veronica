"""Nothing she keeps on the side stops her from starting: diagnostics that can't be
written leave the run without them, and a voices file she can't read is set aside."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import data_acl
from app.core.config import Settings
from app.core.sealed import seal
from app.main import create_app
from app.telemetry.runtime import start_telemetry, stop_telemetry
from app.telemetry.settings import TelemetrySettings


def test_diagnostics_that_cannot_be_written_leave_the_run_without_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A file where the folder should be: the black box couldn't make its folder, and
    the start stopped (mutation: let the error through)."""
    blocked = tmp_path / "telemetry"
    blocked.write_text("in the way", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        runtime = start_telemetry(TelemetrySettings(dir=str(blocked)))

    assert not runtime.enabled and runtime.perf is None
    assert "diagnostics are off for this run" in caplog.text
    stop_telemetry(runtime)  # and stopping it is harmless


def test_a_black_box_that_fails_closes_the_timing_log_it_started_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: leave the timing log's thread running)"""
    from app.telemetry import runtime as runtime_module

    closed: list[bool] = []
    real = runtime_module.PerfLogger

    class Watched(real):  # type: ignore[misc, valid-type]
        def close(self, timeout: float = 2.0) -> None:
            closed.append(True)
            super().close(timeout)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(runtime_module, "PerfLogger", Watched)
    monkeypatch.setattr(runtime_module, "BlackBox", refuse)

    runtime = start_telemetry(TelemetrySettings(dir=str(tmp_path / "telemetry")))

    assert not runtime.enabled and closed == [True]


def test_the_app_starts_with_its_diagnostics_folder_blocked(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    async def keep_private(_path: Path) -> data_acl.Kept:
        return data_acl.Kept("ok")

    as_set_up.setattr(data_acl, "keep_private", keep_private)
    data = tmp_path / "data"
    data.mkdir()
    (data / "telemetry").write_text("in the way", encoding="utf-8")

    with TestClient(
        create_app(Settings(data_dir=str(data))), base_url="http://localhost"
    ) as client:
        health = client.get("/health").json()

    assert health["status"] == "ok"


@pytest.mark.parametrize(
    "damaged", ["{not json", '{"voice_id": "x"}', "[42]", '[{"name": "no id"}]']
)
def test_a_voices_file_she_cannot_read_is_set_aside_and_said(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch, damaged: str
) -> None:
    """It stopped the backend (mutations: let the error through; start silently; write
    the next voice over it)."""

    async def keep_private(_path: Path) -> data_acl.Kept:
        return data_acl.Kept("ok")

    as_set_up.setattr(data_acl, "keep_private", keep_private)
    voices = tmp_path / "data" / "voices"
    voices.mkdir(parents=True)
    (voices / "voices.json").write_text(damaged, encoding="utf-8")

    app = create_app(Settings(data_dir=str(tmp_path / "data")))
    with TestClient(app, base_url="http://localhost") as client:
        health = client.get("/health").json()
        registry = app.state.voice_runtime.voice_service._registry

    assert {"code": "voices_damaged", "detail": {}} in health["problems"]
    assert registry.list() == []
    (aside,) = voices.glob("voices.json.damaged-*")
    assert aside.read_text(encoding="utf-8") == damaged  # kept for whoever can mend it
    assert not (voices / "voices.json").exists()


def test_a_voices_file_that_cannot_be_set_aside_is_never_written_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: save over it)"""
    from app.services.voice import voices as voices_module
    from app.services.voice.voices import Voice, VoiceRegistry

    folder = tmp_path / "voices"
    folder.mkdir()
    (folder / "voices.json").write_text("{not json", encoding="utf-8")

    def stuck(*_args: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(voices_module.os, "replace", stuck)
    registry = VoiceRegistry(folder)

    with pytest.raises(OSError):
        registry.add(Voice(voice_id="rowan", name="Rowan", reference_wav_path="rowan.wav"))
    assert (folder / "voices.json").read_text(encoding="utf-8") == "{not json"
    assert registry.damaged and registry.stuck
    assert registry.list() == []  # nothing listed that isn't saved


def test_a_voices_file_stuck_where_it_is_is_said_in_its_own_words(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """Not "set aside": it wasn't, and no voice can be saved until it is (mutation: one
    code for both)."""

    async def keep_private(_path: Path) -> data_acl.Kept:
        return data_acl.Kept("ok")

    as_set_up.setattr(data_acl, "keep_private", keep_private)
    data = tmp_path / "data"
    data.mkdir()
    (data / "voices").write_text("in the way", encoding="utf-8")  # no folder can be made

    with TestClient(
        create_app(Settings(data_dir=str(data))), base_url="http://localhost"
    ) as client:
        codes = [problem["code"] for problem in client.get("/health").json()["problems"]]

    assert codes == ["voices_unusable"]


def _voices_problems(as_set_up: pytest.MonkeyPatch, voices: Path) -> list[Any]:
    async def keep_private(_path: Path) -> data_acl.Kept:
        return data_acl.Kept("ok")

    as_set_up.setattr(data_acl, "keep_private", keep_private)
    with TestClient(
        create_app(Settings(data_dir=str(voices.parent))), base_url="http://localhost"
    ) as client:
        problems: list[Any] = client.get("/health").json()["problems"]
    return problems


def test_sealed_voices_without_their_key_are_said_and_she_still_starts(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """Locked, not made again: a new key would lose them for good (mutation: say
    nothing)."""
    voices = tmp_path / "data" / "voices"
    voices.mkdir(parents=True)
    (voices / "rowan.wav").write_bytes(seal(b"RIFF clip", os.urandom(32), "rowan.wav"))

    problems = _voices_problems(as_set_up, voices)

    assert {"code": "voices_locked", "detail": {"reason": "key_missing"}} in problems
    assert not (voices / "voices.key").exists()


def test_a_voices_key_file_she_cant_read_is_said_as_a_folder_she_cant_use(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """Its steps, not a lock's (mutation: say it as locked)."""
    voices = tmp_path / "data" / "voices"
    (voices / "voices.key").mkdir(parents=True)  # a folder where the key file should be

    codes = [problem["code"] for problem in _voices_problems(as_set_up, voices)]

    assert codes == ["voices_unusable"]
