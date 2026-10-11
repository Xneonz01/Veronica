"""Writing a file through the real kernel: a write that couldn't happen is refused before
anyone is asked, and the prompt names where the file really goes (`files_write.py`)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.files import register_files
from app.capabilities.protected import Protected
from app.core.config_capabilities import CapabilitySettings
from app.core.event_bus import InProcessEventBus
from app.kernel.capability import CapabilityRequest, CapabilityResult
from app.kernel.confirm import ConfirmationBroker
from app.kernel.kernel import CapabilityKernel
from app.kernel.policy import PolicyStore
from app.kernel.registry import CapabilityRegistry
from app.services.voice.realtime.spoken_confirm import _question


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **row: Any) -> None:
        self.rows.append(row)

    def record_detached(self, **row: Any) -> None:
        self.rows.append(row)


class Desk:
    """The kernel with the real file capabilities in `root` (and any `more` roots), and
    every prompt it raises."""

    def __init__(self, root: Path, install: Path, *more: Path) -> None:
        bus = InProcessEventBus()
        self.prompts: list[dict[str, Any]] = []

        async def capture(event: Any) -> None:
            self.prompts.append(event.payload)

        bus.subscribe("capability.confirm_required", capture)
        registry = CapabilityRegistry()
        roots = [str(folder) for folder in (root, *more)]
        settings = CapabilitySettings(file_roots=roots, max_write_bytes=64)
        register_files(registry, settings, Protected.for_install(install))
        self.broker = ConfirmationBroker(bus, timeout_s=5.0)
        self.audit = _Audit()
        self.kernel = CapabilityKernel(registry, PolicyStore(bus), self.broker, self.audit, bus)

    async def write(self, path: Path | str, content: str, **extra: Any) -> CapabilityResult:
        """Ask for the write; say yes to the prompt if one comes."""
        args = {"path": str(path), "content": content, **extra}
        request = CapabilityRequest(name="files.write", args=args, actor="veronica:chat")
        task = asyncio.create_task(self.kernel.execute(request))
        for _ in range(100):
            if task.done() or self.broker.pending():
                break
            await asyncio.sleep(0.01)
        for pending in self.broker.pending():
            self.broker.resolve(pending.token, True, via="test")
        return await task


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    (tmp_path / "files" / "notes").mkdir(parents=True)
    return Desk(tmp_path / "files", tmp_path / "install")


@pytest.mark.parametrize(
    ("where", "content", "code"),
    [
        ("notes/long.md", "x" * 65, "file_too_large"),
        ("missing/folder/new.md", "short", "parent_missing"),
        ("notes/exists.md", "short", "already_exists"),
        ("../outside.md", "short", "path_outside_roots"),
    ],
)
async def test_a_write_that_cant_happen_is_refused_before_anyone_is_asked(
    tmp_path: Path, desk: Desk, where: str, content: str, code: str
) -> None:
    """A yes to it only led to "That's more than I can write at once" (mutation: check
    nothing before asking)."""
    (tmp_path / "files" / "notes" / "exists.md").write_text("kept", encoding="utf-8")

    result = await desk.write(tmp_path / "files" / where, content)

    assert (result.ok, result.code) == (False, code)
    assert desk.prompts == []
    assert (tmp_path / "files" / "notes" / "exists.md").read_text(encoding="utf-8") == "kept"
    assert len(desk.audit.rows) == 1  # refused on the record


async def test_one_of_her_own_files_is_refused_before_anyone_is_asked(tmp_path: Path) -> None:
    """A root holding the installation doesn't open her data to a write."""
    install = tmp_path / "install"
    (install / "data").mkdir(parents=True)
    desk = Desk(tmp_path, install)

    result = await desk.write(install / "data" / "policy.json", "{}")

    assert (result.ok, result.code, desk.prompts) == (False, "path_protected", [])


async def test_the_prompt_names_where_the_file_really_goes(tmp_path: Path, desk: Desk) -> None:
    """Not the path as the model wrote it, with its ".." (mutation: name `args.path`)."""
    written = tmp_path / "files" / "notes" / ".." / "notes" / "todo.md"
    real = (tmp_path / "files" / "notes" / "todo.md").resolve()

    result = await desk.write(written, "Book the roofer")

    assert result.ok
    assert [prompt["summary"] for prompt in desk.prompts] == [f"write 15 characters to {real}"]
    assert real.read_text(encoding="utf-8") == "Book the roofer"


async def test_replacing_a_file_says_so(tmp_path: Path, desk: Desk) -> None:
    """(mutation: call it a write whatever is there)"""
    target = tmp_path / "files" / "notes" / "todo.md"
    target.write_text("old", encoding="utf-8")

    result = await desk.write(target, "new list", overwrite=True)

    assert result.ok
    assert desk.prompts[0]["summary"] == f"replace {target.resolve()} with 8 characters"
    assert target.read_text(encoding="utf-8") == "new list"


async def test_the_write_is_checked_again_after_the_yes(tmp_path: Path, desk: Desk) -> None:
    """A file that appeared while she asked isn't overwritten (mutation: trust the check
    made before asking)."""
    target = tmp_path / "files" / "notes" / "todo.md"
    request = CapabilityRequest(
        name="files.write", args={"path": str(target), "content": "mine"}, actor="veronica:chat"
    )
    task = asyncio.create_task(desk.kernel.execute(request))
    for _ in range(100):
        if desk.broker.pending():
            break
        await asyncio.sleep(0.01)
    assert desk.broker.pending(), "the write never asked, so nothing was checked again"
    target.write_text("theirs", encoding="utf-8")
    for pending in desk.broker.pending():
        desk.broker.resolve(pending.token, True, via="test")

    result = await task

    assert (result.ok, result.code) == (False, "already_exists")
    assert target.read_text(encoding="utf-8") == "theirs"


def test_a_call_still_says_only_the_file_name(tmp_path: Path) -> None:
    """The prompt now names the full path; aloud it is still just the file, and a
    replacement says so: on a call it is the only question heard (mutation: "write to"
    either way)."""
    real = (tmp_path / "notes" / "todo.md").resolve()

    assert _question("files.write", f"write 1,204 characters to {real}") == (
        "That'll write to todo.md. Want me to?"
    )
    assert _question("files.write", f"replace {real} with 1,204 characters") == (
        "That'll replace todo.md. Want me to?"
    )


async def test_a_folder_is_not_a_file_to_write(tmp_path: Path, desk: Desk) -> None:
    """Refused before anyone is asked, in its own words; it once asked to "replace" the
    folder, then failed after the yes (mutation: no folder check)."""
    folder = tmp_path / "files" / "notes"

    for overwrite in (False, True):
        result = await desk.write(folder, "a list", overwrite=overwrite)
        assert (result.ok, result.code, result.detail) == (
            False,
            "path_invalid",
            "That's a folder, not a file.",
        )
    assert desk.prompts == [] and folder.is_dir()


async def test_a_yes_writes_where_the_prompt_said_and_the_record_says_where(
    tmp_path: Path,
) -> None:
    """Resolved again after the answer, a relative path went to another of her folders
    that had gained a file of that name meanwhile, and replaced it; and the record kept
    the path as the model wrote it (mutation: don't pin the path)."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    desk = Desk(first, tmp_path / "install", second)
    args = {"path": "plan.md", "content": "NEW", "overwrite": True}
    request = CapabilityRequest(name="files.write", args=args, actor="veronica:chat")
    task = asyncio.create_task(desk.kernel.execute(request))
    for _ in range(100):
        if desk.broker.pending():
            break
        await asyncio.sleep(0.01)
    (second / "plan.md").write_text("the user's own plan", encoding="utf-8")
    for pending in desk.broker.pending():
        desk.broker.resolve(pending.token, True, via="test")

    result = await task

    planned = first.resolve() / "plan.md"
    assert [prompt["summary"] for prompt in desk.prompts] == [f"write 3 characters to {planned}"]
    assert result.ok
    assert planned.read_text(encoding="utf-8") == "NEW"
    assert (second / "plan.md").read_text(encoding="utf-8") == "the user's own plan"
    assert desk.audit.rows[-1]["args"]["path"] == str(planned)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drops a name's trailing dots")
@pytest.mark.parametrize("name", ["CLAUDE.md.", "AGENTS.md. ."])
async def test_a_name_windows_would_change_is_not_written(
    tmp_path: Path, desk: Desk, name: str
) -> None:
    """ "CLAUDE.md." is written as CLAUDE.md, past the check on her own files, which
    compares names as given (mutation: take trailing dots and spaces)."""
    result = await desk.write(tmp_path / "files" / name, "planted")

    assert (result.ok, result.code, desk.prompts) == (False, "path_invalid", [])
    assert result.detail == "That isn't a plain file path."
    assert sorted(path.name for path in (tmp_path / "files").iterdir()) == ["notes"]
