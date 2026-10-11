"""Launch a local application by allow-listed name.

Kept narrow on purpose. Two things are never done here:
  * No `shell=True`: the name would become a command line.
  * No `os.startfile` on an arbitrary path: that runs by file association, so a
    `.bat`, `.ps1` or `.lnk` in an allowed folder would mean arbitrary code
    execution behind an "open an app" label.
Only names in the configured allow-list can launch, and no arguments are accepted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.config_capabilities import CapabilitySettings
from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.errors import Code
from app.kernel.registry import Capability, CapabilityRegistry

logger = logging.getLogger(__name__)


class OpenArgs(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(description="Which allowed application to open, by name.")


def _search_path(name: str) -> Path | None:
    """Find a bare program name on PATH, never in the current directory.

    Both CreateProcess and `shutil.which` look in the current directory before PATH
    on Windows, so an executable planted next to the server would be picked over the
    real one. Only absolute PATH entries are searched here.
    """
    suffixes = [""]
    if sys.platform == "win32" and not Path(name).suffix:
        pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        suffixes = [ext.lower() for ext in pathext.split(os.pathsep) if ext]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(entry.strip().strip('"'))
        if not entry.strip() or not directory.is_absolute():
            continue
        for suffix in suffixes:
            candidate = directory / f"{name}{suffix}"
            if candidate.is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
                return candidate
    return None


def _resolve_executable(value: str) -> Path | None:
    """Turn an allow-list entry into an absolute, existing executable, once, at startup.

    An absolute path (environment variables allowed) is used as is. A bare name is
    looked up on PATH without the current directory. A relative path is refused,
    because its meaning would depend on where the server was started.
    """
    expanded = os.path.expandvars(value.strip())
    if not expanded:
        return None
    candidate = Path(expanded)
    if not candidate.is_absolute():
        if candidate.name != expanded:
            return None
        found = _search_path(expanded)
        if found is None:
            return None
        candidate = found
    candidate = candidate.resolve(strict=False)
    return candidate if candidate.is_file() else None


def _named(allowlist: dict[str, Path]) -> str:
    """The names the model may ask for, as it must write them."""
    return ", ".join(sorted(allowlist)) or "nothing yet"


def _launch(executable: Path) -> subprocess.Popen[bytes]:
    """Start the app on its own, with none of the backend's console streams.

    `subprocess`, run on a worker thread, not asyncio's: on Windows asyncio starts a
    process only on the proactor event loop, and uvicorn runs the selector loop under
    `--reload`, where every launch failed (bug 14).
    """
    flags = 0
    if sys.platform == "win32":
        # Detach so the app outlives the request and never inherits our console.
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [str(executable)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def register_apps(registry: CapabilityRegistry, settings: CapabilitySettings) -> None:
    allowlist: dict[str, Path] = {}
    for key, value in settings.app_allowlist.items():
        executable = _resolve_executable(value)
        if executable is None:
            # Dropped now rather than at launch time: "I can only open X, Y" must list
            # what will actually open, and an entry that cannot resolve never will.
            logger.warning("app_allowlist entry %r -> %r does not resolve; skipping", key, value)
            continue
        allowlist[key.strip().lower()] = executable
    # What was opened, held until it is seen to have exited, as asyncio's loop held what
    # it started: dropped while running, a process object warns, and on POSIX one that
    # exited stays a zombie until it is waited for.
    running: list[subprocess.Popen[bytes]] = []

    async def open_app(args: OpenArgs) -> CapabilityResult:
        executable = allowlist.get(args.name.strip().lower())
        if executable is None:
            return CapabilityResult(
                ok=False,
                code=Code.app_not_allowed,
                detail=f"I can only open: {_named(allowlist)}.",
            )
        process = await asyncio.to_thread(_launch, executable)
        running[:] = [started for started in running if started.poll() is None] + [process]
        # A successful spawn doesn't prove a window appeared. The process isn't
        # awaited because the app should outlive this request.
        return CapabilityResult(ok=True, detail=f"Opened {args.name}.")

    registry.register(
        Capability(
            name="apps.open",
            voice_exposed=True,
            # By name: without them the model guessed ("File Explorer" for
            # `explorer`) and was refused.
            description=f"Open one of the allowed applications: {_named(allowlist)}.",
            args_model=OpenArgs,
            risk=RiskTier.guarded,
            handler=open_app,
            summarize=lambda a: f"open {a.name}",
        )
    )
