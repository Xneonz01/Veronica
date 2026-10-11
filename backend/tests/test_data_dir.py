"""Her data folder: one setting says where it is, and every start keeps it private.

The folder's permissions themselves are tested against the real ones in
`test_data_acl.py`; this is the part around them, on every platform.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import left_behind, main
from app.capabilities.protected import protected_for
from app.core import data_acl
from app.core.config import STORES, Settings, get_settings
from app.core.config_memory import MemorySettings
from app.core.win_security import DataAclError
from app.main import create_app
from app.paths import INSTALL_ROOT


class _Box:
    """A black box that keeps what it is told."""

    def __init__(self) -> None:
        self.notes: list[tuple[str, str, dict[str, Any]]] = []

    def record(self, level: str, event: str, **fields: Any) -> None:
        self.notes.append((level, event, fields))


def test_every_store_is_kept_in_the_data_folder_unless_it_names_a_place(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """Moving the data folder moves all of her data (mutation: stores keep their own
    defaults)."""
    settings = Settings(data_dir=str(tmp_path))
    assert Path(settings.memory.db_path) == tmp_path / "memory" / "memory.db"
    assert Path(settings.voice.voices_dir) == tmp_path / "voices"
    assert Path(settings.realtime.metrics_path) == tmp_path / "metrics" / "voice_turns.jsonl"
    assert Path(settings.telemetry.dir) == tmp_path / "telemetry"

    as_set_up.setenv("VERONICA_MEMORY__DB_PATH", str(tmp_path / "elsewhere.db"))
    settings = Settings(data_dir=str(tmp_path))
    assert Path(settings.memory.db_path) == tmp_path / "elsewhere.db"  # where it says
    assert Path(settings.voice.voices_dir) == tmp_path / "voices"


def test_by_default_every_store_is_where_it_always_was(as_set_up: pytest.MonkeyPatch) -> None:
    """In the installation's data folder, as before 0.29.0: nothing moves on its own."""
    settings = Settings()
    assert Path(settings.memory.db_path) == INSTALL_ROOT / "data" / "memory" / "memory.db"
    assert Path(settings.voice.voices_dir) == INSTALL_ROOT / "data" / "voices"
    assert Path(settings.telemetry.dir) == INSTALL_ROOT / "data" / "telemetry"


def test_settings_no_longer_read_are_ignored_not_refused(as_set_up: pytest.MonkeyPatch) -> None:
    """Removed in 0.29.0; a configuration that still has them keeps working."""
    as_set_up.setenv("VERONICA_TTS__ENGINE", "xtts")
    as_set_up.setenv("VERONICA_GPU__MODE", "idle")
    settings = Settings()
    assert not hasattr(settings.tts, "engine") and not hasattr(settings.gpu, "mode")


def test_the_app_uses_the_settings_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its data folder, and what was left behind looked for against them (mutations:
    the lifespan reads the cached settings instead; the start leaves that check out)."""
    checked: list[Settings] = []
    warn = left_behind.warn

    async def keep_private(_path: Path) -> data_acl.Kept:
        return data_acl.Kept("ok")

    def check(given: Settings) -> list[str]:
        checked.append(given)
        return warn(given)

    monkeypatch.setattr(data_acl, "keep_private", keep_private)
    monkeypatch.setattr(left_behind, "warn", check)
    settings = Settings(data_dir=str(tmp_path / "given"))

    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.app.state.settings is settings  # type: ignore[attr-defined]
        persona_path = client.app.state.persona._path  # type: ignore[attr-defined]

    assert persona_path == tmp_path / "given" / "persona.json"
    assert checked == [settings]


def test_a_section_handed_in_is_placed_but_never_changed(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """The place a store gets from the data folder is a default, not a setting: the same
    section in other settings goes to their data folder (mutations: write it on the
    section handed in; mark it as set)."""
    section = MemorySettings()
    first = Settings(memory=section, data_dir=str(tmp_path / "first"))
    second = Settings(memory=first.memory, data_dir=str(tmp_path / "second"))

    assert Path(first.memory.db_path) == tmp_path / "first" / "memory" / "memory.db"
    assert Path(second.memory.db_path) == tmp_path / "second" / "memory" / "memory.db"
    assert section.db_path == MemorySettings().db_path
    assert "db_path" not in second.memory.model_fields_set


def test_data_dir_hangs_off_the_installation_unless_absolute(tmp_path: Path) -> None:
    relative = Settings(data_dir="elsewhere").data_path()
    assert relative.name == "elsewhere"
    assert relative.parent == Settings(data_dir="data").data_path().parent
    assert Settings(data_dir=str(tmp_path)).data_path() == tmp_path


_DRIVE_RELATIVE = ["\\data", "D:data"] if sys.platform == "win32" else []


@pytest.mark.parametrize("value", ["", "   ", ".", "..", "data/..", *_DRIVE_RELATIVE])
def test_a_data_dir_that_is_not_a_folder_of_its_own_is_refused(value: str) -> None:
    """Empty, "." or ".." put her files in the installation or above it; "\\data" and
    "D:data" hang off whatever drive or folder the process is in (mutation: accept it)."""
    with pytest.raises(ValidationError):
        Settings(data_dir=value)


@pytest.mark.parametrize("variable", [f"VERONICA_{s.upper()}__{f.upper()}" for s, f, _ in STORES])
@pytest.mark.parametrize("value", ["", ".", "..", *_DRIVE_RELATIVE])
def test_a_store_that_names_no_place_of_its_own_is_refused(
    as_set_up: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """Held to the data folder's rule: empty, the memory file was the installation
    itself, and voices.json would have landed among the code (mutation: any value)."""
    as_set_up.setenv(variable, value)
    with pytest.raises(ValidationError):
        Settings()


def test_a_place_is_read_without_the_spaces_around_it(tmp_path: Path) -> None:
    """(mutation: check the stripped value, keep the one with spaces)"""
    assert Settings(data_dir=f"  {tmp_path}  ").data_path() == tmp_path


def test_a_relocated_data_folder_is_hidden_from_her_file_tools(tmp_path: Path) -> None:
    """Moved out of the installation, it is still her private data (mutation: leave
    `data_dir` out of `protected_for`)."""
    moved = tmp_path / "veronica-data"
    protected = protected_for(Settings(data_dir=str(moved)))

    assert protected.hides(Path(os.path.realpath(moved / "persona.json")))


def test_the_app_keeps_its_data_folder_private_and_keeps_its_files_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked at start-up, for the configured folder; the personality, policy and
    attachments live in it (in tests, the session's scratch folder)."""
    asked: list[Path] = []

    async def keep_private(path: Path) -> None:
        asked.append(path)

    monkeypatch.setattr(data_acl, "keep_private", keep_private)
    data_dir = get_settings().data_path()

    with TestClient(create_app(), base_url="http://localhost") as client:
        persona_path = client.app.state.persona._path  # type: ignore[attr-defined]

    assert asked == [data_dir]
    assert persona_path == data_dir / "persona.json"


def test_the_folder_is_private_before_the_black_box_writes_into_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The black box's own files may be inside it, and a fresh install read as
    "repaired"; what was done is recorded once the black box is up (mutation: start
    the black box first)."""
    order: list[str] = []
    box = _Box()
    start_telemetry = main.start_telemetry

    async def keep_private(_path: Path) -> data_acl.Kept:
        order.append("private")
        return data_acl.Kept("created")

    def start_after(*args: Any) -> Any:
        order.append("black box")
        return start_telemetry(*args)

    monkeypatch.setattr(data_acl, "keep_private", keep_private)
    monkeypatch.setattr(main, "start_telemetry", start_after)
    monkeypatch.setattr(data_acl, "current_blackbox", lambda: box)

    with TestClient(create_app(), base_url="http://localhost"):
        pass

    assert order == ["private", "black box"]
    assert box.notes == [("info", "data.private", {"outcome": "created"})]


@pytest.mark.parametrize("error", [DataAclError("could not set the permissions"), TypeError()])
async def test_a_folder_it_cannot_secure_is_said_and_the_start_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Whatever goes wrong, only the error's type is kept: its message can carry the
    folder's path (mutation: catch only the module's own error)."""
    box = _Box()

    def refuse(_path: Path) -> str:
        raise error

    monkeypatch.setattr(data_acl, "current_blackbox", lambda: box)
    monkeypatch.setattr(data_acl, "ensure", refuse)

    data_acl.record(await data_acl.keep_private(tmp_path / "data"))

    kind = type(error).__name__
    assert box.notes == [("warn", "data.private", {"outcome": "failed", "error": kind})]


async def test_each_folder_is_checked_once_a_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests start the app many times over (mutation: forget what was checked)."""
    checked: list[Path] = []
    box = _Box()

    def ensure(path: Path) -> str:
        checked.append(path)
        return "repaired"

    monkeypatch.setattr(data_acl, "current_blackbox", lambda: box)
    monkeypatch.setattr(data_acl, "ensure", ensure)
    folder = tmp_path / "once"

    data_acl.record(await data_acl.keep_private(folder))
    data_acl.record(await data_acl.keep_private(folder))

    assert checked == [folder.resolve()]
    assert box.notes == [("warn", "data.private", {"outcome": "repaired"})]


@pytest.mark.parametrize(
    ("outcome", "level"),
    [
        ("ok", "info"),
        ("created", "info"),
        ("repaired", "warn"),
        ("unsupported", "warn"),
        ("refused", "warn"),
    ],
)
def test_each_outcome_is_recorded_at_its_level(
    monkeypatch: pytest.MonkeyPatch, outcome: Any, level: str
) -> None:
    """A new folder is no warning; one that had drifted, or was left open, is
    (mutation: warn on a new one)."""
    box = _Box()
    monkeypatch.setattr(data_acl, "current_blackbox", lambda: box)

    data_acl.record(data_acl.Kept(outcome))

    assert box.notes == [(level, "data.private", {"outcome": outcome})]


def test_a_folder_holding_more_than_her_data_is_too_wide(tmp_path: Path) -> None:
    """Locked, a drive's root, a home folder or the installation would take everything
    in it along (mutation: drop one of the three)."""
    install = Path(__file__).resolve().parents[2]
    home = Path.home()

    for wide in (Path(tmp_path.anchor), home, home.parent, install, install.parent):
        assert data_acl.too_wide(wide)
    assert not data_acl.too_wide(install / "data")
    assert not data_acl.too_wide(tmp_path / "data")


def test_a_folder_too_wide_is_refused_before_anything_is_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: create the folder before asking)"""
    monkeypatch.setattr(data_acl, "too_wide", lambda _path: True)
    data = tmp_path / "data"

    assert data_acl.ensure(data) == "refused"

    assert not data.exists()
