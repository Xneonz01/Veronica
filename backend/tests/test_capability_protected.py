"""Veronica's own files stay out of reach of her file capabilities, whatever the roots say.

The default root is the installation itself, which holds `data/policy.json`. A
confirmed `files.write` used to be able to rewrite it, loosening what needs approval
at the next start. Each test builds a small installation in a temp folder.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.capabilities.files import register_files
from app.capabilities.paths import PathRefused
from app.capabilities.protected import INSTALL_ROOT, LOCAL_ONLY, Protected, protected_for
from app.core.config import get_settings
from app.core.config_capabilities import CapabilitySettings
from app.kernel.capability import CapabilityResult
from app.kernel.errors import Code
from app.kernel.registry import CapabilityRegistry

_POLICY = '{"files.write": "confirm"}'

_FILES = {
    "data/policy.json": _POLICY,
    "data/persona.json": '{"text": "a secret about me"}',
    ".env": "VERONICA_PROFILE=secret-profile",
    ".git/hooks/pre-commit": "echo hi",
    "configs/default.yaml": "llm: {}",
    "backend/app/main.py": "print('hi')",
    "CLAUDE.md": "rules",
    "VERONICA_MASTER_PLAN.md": "the owner's name",
    "AUDIT_2026-07-28.md": "the owner's machine",
    "scratchpad/probe.py": "the owner's paths",
    "notes.md": "shopping list: tea",
}


async def _run(registry: CapabilityRegistry, name: str, /, **args: object) -> CapabilityResult:
    capability = registry.get(name)
    assert capability is not None
    result = await capability.handler(capability.args_model.model_validate(args))
    assert isinstance(result, CapabilityResult)
    return result


def _installation(root: Path, *, data_dirs: tuple[Path, ...] = ()) -> CapabilityRegistry:
    for relative, content in _FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    registry = CapabilityRegistry()
    # The default configuration: the only root is the installation itself.
    settings = CapabilitySettings(file_roots=[str(root)])
    register_files(registry, settings, Protected.for_install(root, data_dirs))
    return registry


def _refused(result: CapabilityResult) -> bool:
    return result.ok is False and result.code == Code.path_protected


async def test_her_policy_file_cannot_be_rewritten(tmp_path: Path) -> None:
    registry = _installation(tmp_path)
    policy = tmp_path / "data" / "policy.json"
    loosened = '{"files.write": "auto_allow"}'
    result = await _run(registry, "files.write", path=str(policy), content=loosened, overwrite=True)
    assert _refused(result)
    assert policy.read_text(encoding="utf-8") == _POLICY


async def test_private_data_and_secrets_cannot_be_read(tmp_path: Path) -> None:
    registry = _installation(tmp_path)
    for relative in ("data/persona.json", ".env", ".git/hooks/pre-commit"):
        assert _refused(await _run(registry, "files.read", path=str(tmp_path / relative)))
    assert _refused(await _run(registry, "files.list", path=str(tmp_path / "data")))


async def test_a_git_hook_cannot_be_planted(tmp_path: Path) -> None:
    registry = _installation(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "post-checkout"
    result = await _run(registry, "files.write", path=str(hook), content="curl evil")
    assert _refused(result) and not hook.exists()


async def test_settings_and_source_can_be_read_but_not_changed(tmp_path: Path) -> None:
    registry = _installation(tmp_path)
    for relative in ("configs/default.yaml", "backend/app/main.py"):
        path = tmp_path / relative
        assert (await _run(registry, "files.read", path=str(path))).ok
        written = await _run(registry, "files.write", path=str(path), content="x", overwrite=True)
        assert _refused(written)
        assert path.read_text(encoding="utf-8") == _FILES[relative]


async def test_ordinary_files_in_the_installation_still_work(tmp_path: Path) -> None:
    registry = _installation(tmp_path)
    assert (await _run(registry, "files.read", path=str(tmp_path / "notes.md"))).ok
    written = await _run(registry, "files.write", path=str(tmp_path / "todo.md"), content="tea")
    assert written.ok and (tmp_path / "todo.md").read_text(encoding="utf-8") == "tea"


async def test_listings_and_searches_leave_her_data_out(tmp_path: Path) -> None:
    registry = _installation(tmp_path)
    listing = await _run(registry, "files.list", path=str(tmp_path))
    names = {entry["name"] for entry in listing.data["entries"]}
    assert {"backend", "configs", "notes.md"} <= names
    assert not names & {"data", ".env", ".git", "CLAUDE.md", "VERONICA_MASTER_PLAN.md"}

    for secret in ("secret", "files.write", "owner"):
        found = await _run(
            registry, "files.search", path=str(tmp_path), query=secret, in_content=True
        )
        assert found.data["hits"] == []
    tea = await _run(registry, "files.search", path=str(tmp_path), query="tea", in_content=True)
    assert [Path(hit["path"]).name for hit in tea.data["hits"]] == ["notes.md"]


async def test_the_notes_kept_on_this_computer_are_out_of_reach(tmp_path: Path) -> None:
    """A plan that names the owner, working notes and agent instructions stay local;
    her file tools could read them (mutation: only dot-files hidden)."""
    registry = _installation(tmp_path)
    for relative in (
        "VERONICA_MASTER_PLAN.md",
        "AUDIT_2026-07-28.md",
        "CLAUDE.md",
        "scratchpad/probe.py",
    ):
        assert _refused(await _run(registry, "files.read", path=str(tmp_path / relative)))
    written = await _run(registry, "files.write", path=str(tmp_path / "AUDIT_new.md"), content="x")
    assert _refused(written) and not (tmp_path / "AUDIT_new.md").exists()


def test_what_the_repository_keeps_private_is_hidden() -> None:
    """Every top-level name in `.gitignore`'s private section is one the file tools
    hide: a note added there and not here would be readable (mutation: drop one)."""
    lines = (INSTALL_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    start = lines.index("# Local tooling and private notes; kept on this machine, not published")
    section = [line.rstrip("/") for line in lines[start + 1 :]]
    private = section[: section.index("")] if "" in section else section
    protected = protected_for(get_settings())

    assert private and LOCAL_ONLY
    for name in private:
        assert protected.hides(INSTALL_ROOT / name / "x"), name
    assert protected.hides(INSTALL_ROOT / "claude.md")  # Windows folds the case


async def test_a_data_location_outside_the_installation_is_hidden_too(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    memory_dir = elsewhere / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "memory.db").write_text("rows", encoding="utf-8")
    registry = CapabilityRegistry()
    settings = CapabilitySettings(file_roots=[str(elsewhere)])
    protected = Protected.for_install(tmp_path / "veronica", [memory_dir])
    register_files(registry, settings, protected)
    assert _refused(await _run(registry, "files.read", path=str(memory_dir / "memory.db")))


async def test_a_link_into_her_data_is_refused(tmp_path: Path) -> None:
    registry = _installation(tmp_path)
    link = tmp_path / "harmless.json"
    try:
        os.symlink(tmp_path / "data" / "policy.json", link)
    except OSError:
        pytest.skip("creating symlinks needs extra rights on this machine")
    written = await _run(registry, "files.write", path=str(link), content="{}", overwrite=True)
    assert _refused(written)
    assert (tmp_path / "data" / "policy.json").read_text(encoding="utf-8") == _POLICY


def test_the_real_configuration_protects_the_real_installation() -> None:
    # Pure path checks: nothing here opens or writes a real file.
    settings = get_settings()
    protected = protected_for(settings)
    assert protected.hides(INSTALL_ROOT / "data" / "policy.json")
    assert protected.hides(INSTALL_ROOT / ".env")
    assert protected.hides(Path(settings.memory.db_path).parent / "memory.db")
    with pytest.raises(PathRefused):
        protected.check(INSTALL_ROOT / "backend" / "app" / "main.py", writing=True)
    protected.check(INSTALL_ROOT / "README.md", writing=True)  # an ordinary file
