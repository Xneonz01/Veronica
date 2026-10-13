"""A network or device path is refused before anything touches the filesystem, and a
write never makes a file Windows runs or opens by itself.

Resolving `\\\\attacker\\share\\x` makes Windows open an SMB connection to that server,
which sends the account's NetNTLMv2 response. `files.read` is `safe` and auto-allowed,
so a page or document that steered the model to read such a path leaked the hash with
no prompt: the refusal used to come after the resolve that made the connection.
"""

from __future__ import annotations

import ntpath
import os
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.files_write import register_write
from app.capabilities.paths import PathRefused, resolve_within
from app.core.config_capabilities import CapabilitySettings
from app.kernel.registry import CapabilityRegistry

UNC = [
    "\\\\attacker.example\\share\\x.txt",
    "//attacker.example/share/x.txt",
    "\\/attacker.example/share/x.txt",
    "/\\attacker.example\\share\\x.txt",
    "\\\\?\\UNC\\attacker.example\\share\\x.txt",
    "\\\\?\\C:\\Windows\\win.ini",
    "\\\\.\\COM1",
    "  \\\\attacker.example\\share",
]


@pytest.fixture
def no_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything asks the operating system about a path."""

    def touched(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the filesystem was asked before the path was refused")

    monkeypatch.setattr(os, "stat", touched)
    monkeypatch.setattr(ntpath, "_getfinalpathname", touched, raising=False)
    monkeypatch.setattr(Path, "resolve", touched)
    monkeypatch.setattr(Path, "exists", touched)


@pytest.mark.parametrize("candidate", UNC)
def test_a_network_or_device_path_is_refused_before_any_lookup(
    candidate: str, tmp_path: Path, no_filesystem: None
) -> None:
    """(mutation: drop the check before resolving, leaving only the one after it)"""
    with pytest.raises(PathRefused) as refused:
        resolve_within(candidate, [tmp_path])

    assert refused.value.code == "path_invalid"


@pytest.mark.parametrize("name", ["Notes_\u202etxt.bat", "re\u200bport.md", "a\x00b.txt"])
def test_a_name_with_hidden_characters_is_refused(name: str, tmp_path: Path) -> None:
    """It would read as another name on the prompt (mutation: drop the hidden check)."""
    with pytest.raises(PathRefused, match="hidden characters"):
        resolve_within(str(tmp_path / name), [tmp_path])


@pytest.mark.parametrize(
    "name", ["\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645.txt", "Q3\uf022plan.txt"]
)
def test_a_name_in_a_script_that_joins_or_shared_from_a_mac_is_read(
    name: str, tmp_path: Path
) -> None:
    """A Persian word keeps its letters apart with a zero-width non-joiner; a Mac shares
    `:` as a private-use character (mutation: refuse every format or private-use one)."""
    (tmp_path / name).write_text("x", encoding="utf-8")

    assert resolve_within(str(tmp_path / name), [tmp_path]).name == name


def _write(tmp_path: Path) -> Any:
    registry = CapabilityRegistry()

    def allowed(candidate: str) -> Path:
        return resolve_within(candidate, [tmp_path])

    register_write(registry, CapabilitySettings(file_roots=[str(tmp_path)]), allowed)
    capability = registry.get("files.write")
    assert capability is not None
    return capability


@pytest.mark.parametrize(
    "name", ["notes.lnk", "share.scf", "Desktop.ini", "lib.library-ms", "go.URL", "setup.msi"]
)
async def test_a_file_windows_acts_on_by_itself_is_never_written(name: str, tmp_path: Path) -> None:
    """(mutation: drop the extension rule)"""
    write = _write(tmp_path)
    args = write.args_model(path=str(tmp_path / name), content="[Shell]\nIconFile=x")

    asked = await write.preflight(args)

    assert not asked.ok and asked.code == "path_invalid"
    assert not (tmp_path / name).exists()


@pytest.mark.parametrize("name", ["tool.py", "run.pyw", "old.jse", "job.wsh"])
async def test_a_script_may_be_written_but_the_prompt_says_it_can_run(
    name: str, tmp_path: Path
) -> None:
    """Code is text people write (mutation: refuse scripts too; drop the warning)."""
    write = _write(tmp_path)

    asked = await write.preflight(write.args_model(path=str(tmp_path / name), content="x"))
    plain = await write.preflight(write.args_model(path=str(tmp_path / "notes.md"), content="x"))

    assert asked.ok and asked.detail.endswith("(this file can run as a program)")
    assert plain.ok and "program" not in plain.detail
