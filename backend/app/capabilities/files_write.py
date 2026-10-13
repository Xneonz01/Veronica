"""Writing a file: the one file capability that changes something, so a person is asked
first. Whatever would refuse the write after their yes (too much text, a place outside
the allowed folders or one of her own files, a missing folder, a file that exists) is
checked before they are asked (`Capability.preflight`): a prompt about a write that
can't happen only wastes the answer. The prompt names where the file really goes, the
path resolved, not the one the model wrote. The handler checks it all again when it
writes, since up to 45 s pass in between.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from app.capabilities.paths import PathRefused
from app.core.config_capabilities import CapabilitySettings
from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.errors import Code
from app.kernel.registry import Capability, CapabilityRegistry

# Types nobody writes as text that Windows acts on by itself: run, install, or open with
# a handler, some without a click (a .scf or .library-ms in a folder Explorer shows can
# send the account's hash to a server it names). "Write a text file" never needs them.
_SHELL_ACTIVE = frozenset(
    {
        ".exe", ".com", ".scr", ".pif", ".lnk", ".url", ".scf", ".library-ms",
        ".searchconnector-ms", ".settingcontent-ms", ".msi", ".msp", ".msix", ".appx",
        ".appinstaller", ".application", ".appref-ms", ".theme", ".themepack", ".chm",
        ".msc", ".inf", ".reg", ".cpl", ".dll", ".sys", ".ocx", ".hta", ".jar",
    }
)  # fmt: skip
# Scripts are text people write, so they stay allowed; the prompt says they can run.
_SCRIPTS = frozenset(
    {".js", ".jse", ".ps1", ".psm1", ".bat", ".cmd", ".vbs", ".vbe", ".wsf", ".wsh", ".py", ".pyw"}
)


class WriteArgs(BaseModel):
    model_config = {"extra": "forbid"}
    path: str = Field(
        description=(
            "The file: a full path, or one inside an allowed folder (a new one goes in the first)."
        )
    )
    content: str = Field(description="The full new contents of the file.")
    overwrite: bool = Field(default=False, description="Replace the file if it already exists.")


def register_write(
    registry: CapabilityRegistry,
    settings: CapabilitySettings,
    allowed: Callable[[str], Path],
) -> None:
    """`allowed` resolves a path the write may go to, or raises `PathRefused`."""

    def target(args: WriteArgs) -> Path | CapabilityResult:
        """Where the write would go, or why it can't happen. The path is pinned where it
        resolved, so a yes writes where the prompt said, and the record says where: a
        relative path resolved again after the answer, up to 45 s later, went to another
        of her folders that had gained a file of that name meanwhile."""
        if len(args.content.encode("utf-8")) > settings.max_write_bytes:
            return _refusal(Code.file_too_large, "That's more than I can write at once.")
        try:
            path = allowed(args.path)
        except PathRefused as exc:
            return _refusal(exc.code, exc.detail)
        args.path = str(path)
        if path.suffix.lower() in _SHELL_ACTIVE or path.name.lower() == "desktop.ini":
            return _refusal(
                Code.path_invalid, "I don't write files Windows runs or opens by itself."
            )
        if path.is_dir():
            return _refusal(Code.path_invalid, "That's a folder, not a file.")
        if not path.parent.is_dir():
            return _refusal(Code.parent_missing, "That folder doesn't exist.")
        if path.exists() and not args.overwrite:
            return _refusal(Code.already_exists, "That file already exists.")
        return path

    def asked_about(args: WriteArgs) -> CapabilityResult:
        found = target(args)
        if isinstance(found, CapabilityResult):
            return found
        size = f"{len(args.content):,} characters"
        what = f"replace {found} with {size}" if found.exists() else f"write {size} to {found}"
        if found.suffix.lower() in _SCRIPTS:
            what += " (this file can run as a program)"
        return CapabilityResult(ok=True, detail=what)

    def written(args: WriteArgs) -> CapabilityResult:
        found = target(args)
        if isinstance(found, CapabilityResult):
            return found
        # Atomic: write beside the target then replace, so an interrupted write never
        # truncates a file that was already there.
        temp = found.with_name(f".{found.name}.veronica-tmp")
        try:
            temp.write_text(args.content, encoding="utf-8")
            os.replace(temp, found)
        except OSError:
            # Remove the temp file so a failed write doesn't leave the content in a
            # stray dotfile next to the target.
            temp.unlink(missing_ok=True)
            raise
        return CapabilityResult(ok=True, detail=f"Wrote {found.name} ({len(args.content)} chars).")

    # All of it touches the disk, so none of it runs on the event loop.
    async def write_first(args: WriteArgs) -> CapabilityResult:
        return await asyncio.to_thread(asked_about, args)

    async def write(args: WriteArgs) -> CapabilityResult:
        return await asyncio.to_thread(written, args)

    registry.register(
        Capability(
            name="files.write",
            voice_exposed=True,
            description="Write a text file. Requires the user's approval.",
            args_model=WriteArgs,
            risk=RiskTier.guarded,
            handler=write,
            # What the prompt names comes from the preflight, which always says.
            preflight=write_first,
        )
    )


def _refusal(code: Code, detail: str) -> CapabilityResult:
    return CapabilityResult(ok=False, code=code, detail=detail)
