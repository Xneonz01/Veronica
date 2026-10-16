"""What a tool server may be set up as, the environment it gets, and how its setup and
approvals are kept: sealed, bound to its id, and gone with a change of program."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.mcp.setup import ServerSetup, SetupRefused, environment, launch, program
from app.services.mcp.store import CannotKeep, McpStore

DATA_KEY = os.urandom(32)
TOKEN = "sk-proj-FAKE-0123456789abcdef"


def _setup(**changes: object) -> ServerSetup:
    values: dict[str, object] = {"id": "notes", "name": "Notes", "command": sys.executable}
    return ServerSetup.model_validate({**values, **changes})


@pytest.mark.parametrize("server_id", ["Notes", "no_underscores", "", "x" * 33, "a b"])
def test_an_id_is_lowercase_letters_digits_and_dashes(server_id: str) -> None:
    with pytest.raises(ValidationError):
        _setup(id=server_id)


@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("python.exe", "full path"),
        (str(Path(sys.executable).with_suffix(".py")), ".exe, .cmd or .bat"),
        (str(Path(sys.executable).with_name("nothing-here.exe")), "no program"),
    ],
)
def test_the_program_is_named_in_full_and_must_be_there(command: str, why: str) -> None:
    with pytest.raises(SetupRefused, match=why):
        program(command)


def test_a_batch_file_takes_plain_arguments_only(tmp_path: Path) -> None:
    script = tmp_path / "server.cmd"
    script.write_text("@echo off\n", encoding="utf-8")
    _setup(command=str(script), args=["-y", "@scope/server", "C:\\Notes", "--port=3"])
    for bad in ("a&calc", "%PATH%", 'x"y', "two words", "a|b"):
        with pytest.raises(ValidationError, match="letters, digits"):
            _setup(command=str(script), args=[bad])
    _setup(args=["two words", "a&b"])  # an .exe is started without a shell


@pytest.mark.parametrize(
    "name", ["PYTHONSTARTUP", "pythonpath", "NODE_OPTIONS", "VERONICA_X", "1BAD", "A-B"]
)
def test_some_variables_can_never_be_set(name: str) -> None:
    with pytest.raises(ValidationError):
        _setup(env={name: "x"})


def test_its_environment_is_a_short_list_of_hers_and_its_own() -> None:
    mine = {
        "PATH": "C:\\bin",
        "SystemRoot": "C:\\Windows",
        "SECRET_TOKEN": TOKEN,
        "PYTHONPATH": "x",
    }
    built = environment({"NOTES_KEY": TOKEN}, mine)
    assert built["NOTES_KEY"] == TOKEN
    assert TOKEN not in [v for k, v in built.items() if k != "NOTES_KEY"]
    assert "PYTHONPATH" not in built and "SECRET_TOKEN" not in built
    assert any(k.upper() == "PATH" for k in built)


def test_a_server_works_in_a_folder_of_its_own(tmp_path: Path) -> None:
    assert launch(_setup(), tmp_path).folder == tmp_path / "mcp" / "notes"


def test_a_screen_is_shown_variable_names_never_values() -> None:
    view = _setup(env={"NOTES_KEY": TOKEN}).view()
    assert view["env"] == ["NOTES_KEY"]
    assert TOKEN not in json.dumps(view)


def _store(tmp_path: Path, key: bytes | None = DATA_KEY) -> tuple[McpStore, list[Event]]:
    bus = InProcessEventBus()
    seen: list[Event] = []

    async def note(event: Event) -> None:
        seen.append(event)

    bus.subscribe("mcp.changed", note)
    store = McpStore(bus, tmp_path / "mcp.json", key)
    store.load()
    return store, seen


async def test_arguments_and_variables_are_kept_sealed(tmp_path: Path) -> None:
    store, seen = _store(tmp_path)
    await store.put(_setup(args=[f"--token={TOKEN}"], env={"NOTES_KEY": TOKEN}))
    assert seen and TOKEN not in (tmp_path / "mcp.json").read_text(encoding="utf-8")
    again, _ = _store(tmp_path)
    found = again.get("notes")
    assert found is not None and found.env == {"NOTES_KEY": TOKEN}
    assert found.args == [f"--token={TOKEN}"]


async def test_one_servers_sealed_part_cant_stand_in_for_anothers(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    await store.put(_setup(env={"NOTES_KEY": TOKEN}))
    saved = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    saved["servers"][0]["id"] = "mail"
    (tmp_path / "mcp.json").write_text(json.dumps(saved), encoding="utf-8")
    again, _ = _store(tmp_path)
    assert again.setups() == []


async def test_without_a_data_key_no_arguments_are_kept(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, key=None)
    with pytest.raises(CannotKeep):
        await store.put(_setup(env={"NOTES_KEY": TOKEN}))
    await store.put(_setup())  # nothing to seal: kept


async def test_a_changed_program_loses_its_approvals(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    await store.put(_setup())
    await store.approve("notes", "echo", "pin-1")
    await store.put(_setup(name="Renamed"))
    assert store.approved("notes") == {"echo": "pin-1"}
    await store.put(_setup(args=["--other"]))
    assert store.approved("notes") == {}


async def test_approvals_are_kept_and_withdrawn(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    await store.put(_setup())
    await store.approve("notes", "echo", "pin-1")
    again, _ = _store(tmp_path)
    assert again.approved("notes") == {"echo": "pin-1"}
    await again.approve("notes", "echo", None)
    assert again.approved("notes") == {}
    await again.remove("notes")
    assert again.approved("notes") == {} and again.setups() == []


def test_a_damaged_file_leaves_none(tmp_path: Path) -> None:
    (tmp_path / "mcp.json").write_text("{not json", encoding="utf-8")
    store, _ = _store(tmp_path)
    assert store.setups() == []


@pytest.mark.parametrize("argument", ["two\nlines", "nul\x00here", "x" * 1001])
def test_an_argument_is_one_line_of_plain_text(argument: str) -> None:
    with pytest.raises(ValidationError):
        _setup(args=[argument])


def test_a_quoted_or_padded_batch_file_is_still_held_to_plain_arguments(tmp_path: Path) -> None:
    script = tmp_path / "server.cmd"
    script.write_text("@echo off\n", encoding="utf-8")
    for command in (f'"{script}"', f"{script} ", f'  "{script}"  '):
        with pytest.raises(ValidationError, match="letters, digits"):
            _setup(command=command, args=["a&ver"])
    assert _setup(command=f'"{script}"').command == str(script)  # read once, quotes off


@pytest.mark.parametrize("command", ["\\\\host\\share\\x.exe", "//host/share/x.exe", 'C:\\a"b.exe'])
def test_a_network_path_or_a_stray_quote_is_refused(command: str) -> None:
    with pytest.raises(ValidationError):
        _setup(command=command)


@pytest.mark.parametrize(
    "name", ["JAVA_TOOL_OPTIONS", "npm_config_registry", "UV_INDEX_URL", "NODE_PATH", "PERL5OPT"]
)
def test_variables_that_change_what_runs_or_is_fetched_are_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        _setup(env={name: "x"})


def test_its_own_path_replaces_hers_whatever_its_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    built = environment({"Path": "C:\\mine"}, {"PATH": "C:\\hers"})
    assert [k for k in built if k.upper() == "PATH"] == ["Path"]
