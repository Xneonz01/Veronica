"""The file and app capabilities, exercised through the real registry."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.capabilities import files as files_module
from app.capabilities.apps import register_apps
from app.capabilities.files import register_files
from app.capabilities.protected import Protected
from app.core.config_capabilities import CapabilitySettings
from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.errors import Code
from app.kernel.registry import CapabilityRegistry
from app.services.tools.render import render_result


def _registry(tmp_path: Path, **overrides: object) -> CapabilityRegistry:
    settings = CapabilitySettings(file_roots=[str(tmp_path)], **overrides)  # type: ignore[arg-type]
    registry = CapabilityRegistry()
    # An installation somewhere else, so these tests see only the containment rules.
    # tests/test_capability_protected.py covers Veronica's own files.
    register_files(registry, settings, Protected.for_install(tmp_path.parent / "elsewhere"))
    register_apps(registry, settings)
    return registry


async def _run(registry: CapabilityRegistry, capability_name: str, /, **args: object) -> object:
    # Positional-only: a capability's own args may include `name` (apps.open does).
    capability = registry.get(capability_name)
    assert capability is not None
    return await capability.handler(capability.args_model.model_validate(args))


@pytest.mark.parametrize(
    ("capability", "args"),
    [
        ("files.read", {"path": "note.txt"}),
        ("files.list", {"path": "."}),
        ("files.search", {"path": ".", "query": "note"}),
    ],
)
async def test_no_path_is_resolved_on_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: str, args: dict[str, str]
) -> None:
    """Resolving touches the disk, and a drive gone to sleep would stall a live call
    (mutation: resolve before handing the work to the thread)."""
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    on_loop: list[bool] = []
    resolve = files_module.resolve_within

    def watched(candidate: str, roots: list[Path], *hidden: Any) -> Path:
        on_loop.append(threading.current_thread() is threading.main_thread())
        return resolve(candidate, roots, *hidden)

    monkeypatch.setattr(files_module, "resolve_within", watched)
    paths = {key: str(tmp_path / value) if key == "path" else value for key, value in args.items()}

    result = await _run(_registry(tmp_path), capability, **paths)

    assert result.ok and on_loop and not any(on_loop)  # type: ignore[attr-defined]


async def test_a_write_resolves_its_path_off_the_event_loop_before_and_after_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked before the person is asked and again when it writes, both on a thread
    (mutations: either one on the loop)."""
    on_loop: list[bool] = []
    resolve = files_module.resolve_within

    def watched(candidate: str, roots: list[Path], *hidden: Any) -> Path:
        on_loop.append(threading.current_thread() is threading.main_thread())
        return resolve(candidate, roots, *hidden)

    monkeypatch.setattr(files_module, "resolve_within", watched)
    capability = _registry(tmp_path).get("files.write")
    assert capability is not None and capability.preflight is not None
    args = capability.args_model.model_validate({"path": str(tmp_path / "new.md"), "content": "hi"})

    asked = await capability.preflight(args)
    wrote = await capability.handler(args)

    assert asked.ok and wrote.ok and on_loop == [False, False]


# -- read ----------------------------------------------------------------------


async def test_read_returns_the_file_contents(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello there", encoding="utf-8")
    result = await _run(_registry(tmp_path), "files.read", path=str(tmp_path / "note.txt"))
    assert result.ok and result.data["content"] == "hello there"  # type: ignore[attr-defined]


async def test_read_outside_the_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    registry = _registry(tmp_path / "inner")
    (tmp_path / "inner").mkdir(exist_ok=True)
    result = await _run(registry, "files.read", path=str(outside))
    assert result.ok is False and result.code == Code.path_outside_roots  # type: ignore[attr-defined]


async def test_read_refuses_an_oversize_file(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 500, encoding="utf-8")
    registry = _registry(tmp_path, max_read_bytes=100)
    result = await _run(registry, "files.read", path=str(tmp_path / "big.txt"))
    assert result.ok is False and result.code == Code.file_too_large  # type: ignore[attr-defined]


async def test_read_refuses_a_binary_file(tmp_path: Path) -> None:
    (tmp_path / "app.bin").write_bytes(b"\x00\x01\x02binary")
    result = await _run(_registry(tmp_path), "files.read", path=str(tmp_path / "app.bin"))
    assert result.ok is False and result.code == Code.binary_file  # type: ignore[attr-defined]


async def test_read_of_a_missing_file_is_a_typed_result(tmp_path: Path) -> None:
    result = await _run(_registry(tmp_path), "files.read", path=str(tmp_path / "nope.txt"))
    assert result.ok is False and result.code == Code.not_found  # type: ignore[attr-defined]


# -- list / search -------------------------------------------------------------


async def test_list_returns_the_entries(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    result = await _run(_registry(tmp_path), "files.list", path=str(tmp_path))
    names = {entry["name"] for entry in result.data["entries"]}  # type: ignore[attr-defined]
    assert {"a.txt", "sub"} <= names


async def test_search_finds_by_name_and_by_content(tmp_path: Path) -> None:
    (tmp_path / "todo.md").write_text("remember the milk", encoding="utf-8")
    registry = _registry(tmp_path)

    by_name = await _run(registry, "files.search", path=str(tmp_path), query="todo")
    assert by_name.ok and by_name.data["hits"]  # type: ignore[attr-defined]

    by_content = await _run(
        registry, "files.search", path=str(tmp_path), query="milk", in_content=True
    )
    hits = by_content.data["hits"]  # type: ignore[attr-defined]
    assert hits and hits[0]["line"] == 1


async def test_search_reports_when_it_stopped_early(tmp_path: Path) -> None:
    for index in range(10):
        (tmp_path / f"file{index}.txt").write_text("x", encoding="utf-8")
    registry = _registry(tmp_path, search_max_results=3)
    result = await _run(registry, "files.search", path=str(tmp_path), query="file")
    assert result.data["truncated"] is True  # type: ignore[attr-defined]
    assert len(result.data["hits"]) <= 3  # type: ignore[attr-defined]


# -- write ---------------------------------------------------------------------


async def test_write_creates_the_file(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    result = await _run(_registry(tmp_path), "files.write", path=str(target), content="written")
    assert result.ok  # type: ignore[attr-defined]
    assert target.read_text(encoding="utf-8") == "written"


async def test_write_refuses_to_clobber_without_permission(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original", encoding="utf-8")
    result = await _run(_registry(tmp_path), "files.write", path=str(target), content="new")
    assert result.ok is False and result.code == Code.already_exists  # type: ignore[attr-defined]
    assert target.read_text(encoding="utf-8") == "original"  # untouched


async def test_write_overwrites_when_asked_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original", encoding="utf-8")
    result = await _run(
        _registry(tmp_path), "files.write", path=str(target), content="new", overwrite=True
    )
    assert result.ok  # type: ignore[attr-defined]
    assert target.read_text(encoding="utf-8") == "new"
    assert not any(p.name.endswith("veronica-tmp") for p in tmp_path.iterdir())


async def test_write_refuses_a_missing_folder(tmp_path: Path) -> None:
    target = tmp_path / "nope" / "file.txt"
    result = await _run(_registry(tmp_path), "files.write", path=str(target), content="x")
    assert result.ok is False and result.code == Code.parent_missing  # type: ignore[attr-defined]


async def test_write_is_guarded_and_reads_are_safe(tmp_path: Path) -> None:
    # The risk tier is what forces a confirmation, so it is part of the contract.
    registry = _registry(tmp_path)
    assert registry.get("files.write").risk is RiskTier.guarded  # type: ignore[union-attr]
    assert registry.get("files.read").risk is RiskTier.safe  # type: ignore[union-attr]


# -- apps ----------------------------------------------------------------------


def test_her_folders_are_named_to_the_model(tmp_path: Path) -> None:
    """Bug 29: told nothing, the model guessed at paths (mutation: the description
    alone)."""
    listed = _registry(tmp_path).get("files.list")

    assert listed is not None
    assert listed.description == (
        f"List the files in a folder. The allowed folders: {tmp_path.resolve()}."
    )


async def test_a_file_named_inside_her_folder_is_read(tmp_path: Path) -> None:
    """Bug 29, through the capability (mutation: refuse a relative path)."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "shopping.md").write_text("milk", encoding="utf-8")

    result = await _run(_registry(tmp_path), "files.read", path="notes/shopping.md")

    assert result.ok and result.data["content"] == "milk"  # type: ignore[attr-defined]


async def test_a_file_read_is_recorded_by_where_it_is(tmp_path: Path) -> None:
    """The record kept "notes/shopping.md", and which of her folders held it went unsaid
    (mutation: don't pin the path)."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "shopping.md").write_text("milk", encoding="utf-8")
    capability = _registry(tmp_path).get("files.read")
    assert capability is not None
    args = capability.args_model.model_validate({"path": "notes/shopping.md"})

    result = await capability.handler(args)

    assert result.ok
    assert args.path == str(tmp_path.resolve() / "notes" / "shopping.md")  # type: ignore[attr-defined]


async def test_her_own_folder_leaves_its_namesake_to_be_found(tmp_path: Path) -> None:
    """With the installation among the roots, her data folder made the user's folder of
    the same name ambiguous (mutation: resolve without what is hers)."""
    install, user = tmp_path / "install", tmp_path / "user"
    (install / "data").mkdir(parents=True)
    (user / "data").mkdir(parents=True)
    (user / "data" / "budget.csv").write_text("1,2", encoding="utf-8")
    registry = CapabilityRegistry()
    settings = CapabilitySettings(file_roots=[str(install), str(user)])
    register_files(registry, settings, Protected.for_install(install))

    result = await _run(registry, "files.list", path="data")

    assert result.ok  # type: ignore[attr-defined]
    assert [entry["name"] for entry in result.data["entries"]] == ["budget.csv"]  # type: ignore[attr-defined]


def test_the_apps_it_may_open_are_named_to_the_model(tmp_path: Path) -> None:
    """Bug 33: unnamed, the model asked for "File Explorer" and was refused (mutation:
    the description alone)."""
    names = {"notepad": sys.executable, "explorer": sys.executable}
    listed = _registry(tmp_path, app_allowlist=names).get("apps.open")
    ghost = {"ghost": "definitely-not-installed-xyz.exe"}
    unlisted = _registry(tmp_path, app_allowlist=ghost).get("apps.open")

    assert listed is not None and unlisted is not None
    assert listed.description == "Open one of the allowed applications: explorer, notepad."
    assert unlisted.description == "Open one of the allowed applications: nothing yet."


async def test_opening_an_unlisted_app_is_refused(tmp_path: Path) -> None:
    result = await _run(_registry(tmp_path), "apps.open", name="powershell")
    assert result.ok is False and result.code == Code.app_not_allowed  # type: ignore[attr-defined]


@pytest.mark.parametrize("attempt", ["notepad.exe & calc", "../../evil.bat", "cmd /c del *"])
async def test_app_names_are_matched_exactly_not_interpreted(tmp_path: Path, attempt: str) -> None:
    # There is no shell and no argv, so a crafted name can only fail the allow-list.
    result = await _run(_registry(tmp_path), "apps.open", name=attempt)
    assert result.ok is False and result.code == Code.app_not_allowed  # type: ignore[attr-defined]


async def test_a_utf8_file_larger_than_the_probe_is_not_called_binary(tmp_path: Path) -> None:
    """A character split by the 8 KB binary probe doesn't make a file look binary.

    Decoding the probe whole would reject ordinary non-ASCII text, such as Arabic,
    whenever the cut lands in the middle of a multi-byte character.
    """
    body = "مرحبا بك في فيرونيكا. " * 2000  # comfortably past the 8 KB probe
    target = tmp_path / "arabic.txt"
    target.write_text(body, encoding="utf-8")

    result = await _run(_registry(tmp_path), "files.read", path=str(target))
    assert result.ok is True  # type: ignore[attr-defined]
    assert result.data["content"] == body  # type: ignore[attr-defined]


async def test_real_binary_content_is_still_refused(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\xff\xfe" * 100)
    result = await _run(_registry(tmp_path), "files.read", path=str(target))
    assert result.ok is False and result.code == Code.binary_file  # type: ignore[attr-defined]


async def test_a_truncated_listing_says_so(tmp_path: Path) -> None:
    # Without a marker, the model would present 3 of 10 names as the whole folder.
    for index in range(10):
        (tmp_path / f"file{index}.txt").write_text("x", encoding="utf-8")
    registry = _registry(tmp_path, search_max_results=3)

    result = await _run(registry, "files.list", path=str(tmp_path))
    assert result.data["truncated"] is True  # type: ignore[attr-defined]
    assert len(result.data["entries"]) == 3  # type: ignore[attr-defined]
    assert "of 10" in result.detail  # type: ignore[attr-defined]


async def test_a_complete_listing_is_not_flagged(tmp_path: Path) -> None:
    (tmp_path / "only.txt").write_text("x", encoding="utf-8")
    result = await _run(_registry(tmp_path), "files.list", path=str(tmp_path))
    assert result.data["truncated"] is False  # type: ignore[attr-defined]


async def test_an_allowlisted_app_resolves_to_an_absolute_path(tmp_path: Path) -> None:
    """Allow-listed apps resolve to an absolute path before launch.

    A bare name is resolved by CreateProcess, whose search order reaches the current
    directory before System32, so a planted binary could launch under a trusted name.
    """
    from app.capabilities.apps import _resolve_executable

    resolved = _resolve_executable("notepad.exe")
    assert resolved is not None and resolved.is_absolute() and resolved.is_file()
    assert _resolve_executable("definitely-not-installed-xyz.exe") is None


async def test_an_unresolvable_allowlist_entry_is_dropped_not_offered(tmp_path: Path) -> None:
    registry = _registry(tmp_path, app_allowlist={"ghost": "definitely-not-installed-xyz.exe"})
    result = await _run(registry, "apps.open", name="ghost")
    assert result.ok is False and result.code == Code.app_not_allowed  # type: ignore[attr-defined]


async def test_a_listing_or_search_cut_short_says_so_to_the_model(tmp_path: Path) -> None:
    """Bug 25: the model presented part of a folder as all of it; the note comes first,
    so a cut to fit the voice's share can't take it (mutation: no note; the note last)."""
    for number in range(5):
        (tmp_path / f"note{number}.txt").write_text("hello", encoding="utf-8")
    registry = _registry(tmp_path, search_max_results=3)

    listed = await _run(registry, "files.list", path=str(tmp_path))
    searched = await _run(registry, "files.search", path=str(tmp_path), query="note")

    listing = render_result("files.list", listed)  # type: ignore[arg-type]
    assert "(Only the first 3 of 5 are listed.)\nfile note0.txt" in listing
    assert "matches it didn't reach" in render_result("files.search", searched)  # type: ignore[arg-type]


def test_a_listing_keeps_at_least_one_entry() -> None:
    """At 0, every folder read as empty (mutation: no floor)."""
    with pytest.raises(ValidationError):
        CapabilitySettings(search_max_results=0)


def test_a_search_stopped_before_it_found_anything_is_not_called_empty() -> None:
    """(mutation: "(no matches)" whenever nothing was found)"""
    stopped = CapabilityResult(ok=True, data={"hits": [], "truncated": True})
    finished = CapabilityResult(ok=True, data={"hits": [], "truncated": False})

    said_stopped = render_result("files.search", stopped)
    said_finished = render_result("files.search", finished)
    assert "matches it didn't reach" in said_stopped and "(no matches)" not in said_stopped
    assert "(no matches)" in said_finished and "didn't reach" not in said_finished
