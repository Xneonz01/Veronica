"""How `apps.open` turns an allow-list entry into the program it will run, and starts it."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.capabilities import apps
from app.capabilities.apps import OpenArgs, _resolve_executable, register_apps
from app.core.config_capabilities import CapabilitySettings
from app.kernel.capability import CapabilityResult
from app.kernel.registry import CapabilityRegistry


async def _open(tmp_path: Path, name: str) -> CapabilityResult:
    """`apps.open` on a registry that may open this Python, as "python"."""
    registry = CapabilityRegistry()
    settings = CapabilitySettings(
        file_roots=[str(tmp_path)], app_allowlist={"python": sys.executable}
    )
    register_apps(registry, settings)
    capability = registry.get("apps.open")
    assert capability is not None
    result: CapabilityResult = await capability.handler(OpenArgs(name=name))
    return result


async def test_an_app_opens_on_its_own_with_none_of_her_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked by name, whatever its case; no shell and no arguments (mutation: inherit
    the backend's console streams)."""
    started: list[dict[str, Any]] = []

    class _Started:
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            started.append({"argv": argv, **kwargs})

        def poll(self) -> None:
            return None  # still running

    monkeypatch.setattr(apps.subprocess, "Popen", _Started)
    result = await _open(tmp_path, "Python")

    assert result.ok and result.detail == "Opened Python."
    [launch] = started
    assert launch["argv"] == [str(Path(sys.executable).resolve())]
    assert {launch[stream] for stream in ("stdin", "stdout", "stderr")} == {subprocess.DEVNULL}
    if sys.platform == "win32":
        assert launch["creationflags"] & subprocess.DETACHED_PROCESS


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows' selector loop can't do it")
def test_an_app_opens_on_the_event_loop_uvicorn_reload_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug 14: under `uvicorn --reload` every launch failed, since asyncio starts a process
    on Windows only on the proactor loop (mutation: asyncio's subprocess again). A real
    one: this Python, which reads its empty input and exits."""
    launched: list[subprocess.Popen[bytes]] = []

    def keep(executable: Path) -> subprocess.Popen[bytes]:
        launched.append(real(executable))
        return launched[-1]

    real = apps._launch
    monkeypatch.setattr(apps, "_launch", keep)
    loop = asyncio.SelectorEventLoop()
    try:
        result = loop.run_until_complete(_open(tmp_path, "python"))
    finally:
        loop.close()

    assert result.ok
    assert launched[0].wait(timeout=60) == 0  # it ran, and ended on its own


def _program(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    path.chmod(0o755)
    return path


def test_a_program_planted_in_the_working_directory_is_never_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = _program(tmp_path / "cwd" / "texteditor.exe")
    real = _program(tmp_path / "bin" / "texteditor.exe")
    monkeypatch.chdir(planted.parent)

    monkeypatch.setenv("PATH", str(real.parent))
    assert _resolve_executable("texteditor.exe") == real.resolve()

    monkeypatch.setenv("PATH", "")  # only the planted copy exists now
    assert _resolve_executable("texteditor.exe") is None


def test_relative_path_entries_on_path_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _program(tmp_path / "texteditor.exe")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", ""]))
    assert _resolve_executable("texteditor.exe") is None


def test_a_relative_path_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _program(tmp_path / "tools" / "texteditor.exe")
    monkeypatch.chdir(tmp_path)
    assert _resolve_executable(str(Path("tools") / "texteditor.exe")) is None


def test_an_absolute_entry_can_use_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program = _program(tmp_path / "texteditor.exe")
    monkeypatch.setenv("VERONICA_TEST_APPS", str(tmp_path))
    entry = "%VERONICA_TEST_APPS%" if sys.platform == "win32" else "$VERONICA_TEST_APPS"
    assert _resolve_executable(f"{entry}{os.sep}texteditor.exe") == program.resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT is a Windows lookup rule")
def test_a_name_without_an_extension_is_found_through_pathext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program = _program(tmp_path / "texteditor.exe")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE")
    assert _resolve_executable("texteditor") == program.resolve()
