"""Veronica's own files, which no root setting opens up to her file capabilities.

The default file root is the installation folder. Without this, a confirmed
`files.write` could rewrite `data/policy.json` (loosening what needs approval at the
next start), her settings or source, or a `.git` hook that runs on the next commit;
and the auto-allowed `files.read` could pull her private data into a prompt. The
check applies whatever the roots say, so a root pointed at the installation, or at a
folder holding one of her data locations, doesn't open them either.

Two tiers:

- **hidden**: private state and secrets. Never read, listed, searched or written.
  The data folder and every configured data location, every dot-file or dot-folder at
  the top of the installation (`.env`, `.git`, `.claude`, ...), and the notes that stay
  on this computer (`.gitignore`'s private section): a plan that names the owner,
  working notes, the instruction files for coding agents.
- **read-only**: files that would change how she behaves, or run as code, if
  rewritten. They can be read but never written: settings, the backend and front-end
  source, the task runners.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.capabilities.paths import PathRefused
from app.core.config import Settings
from app.kernel.errors import Code
from app.paths import INSTALL_ROOT, anchored

_READ_ONLY = ("configs", "backend", "frontend", "tasks.py", "Makefile")
# Top-level names kept on this computer and never published (`.gitignore`, "Local
# tooling and private notes"); `tests/test_capability_protected.py` holds the two lists
# together.
LOCAL_ONLY = (
    "AGENTS.md",
    "CLAUDE.md",
    "scratchpad",
    "VERONICA_MASTER_PLAN.md",
    "AUDIT_*.md",
)

_HIDDEN = "That's my own private data, so I leave it alone."
_READ_ONLY_REFUSAL = "That's one of my own files. I can read it, but I won't change it."


def _real(path: Path) -> Path:
    # realpath, not resolve(strict=True): a folder that doesn't exist yet is still
    # protected once it does.
    return Path(os.path.realpath(path))


@dataclass(frozen=True)
class Protected:
    install_root: Path
    hidden: tuple[Path, ...]
    read_only: tuple[Path, ...]

    @classmethod
    def for_install(cls, install_root: Path, data_dirs: Iterable[Path] = ()) -> Protected:
        root = _real(install_root)
        hidden = tuple(dict.fromkeys(_real(path) for path in (root / "data", *data_dirs)))
        return cls(root, hidden, tuple(root / name for name in _READ_ONLY))

    def hides(self, real: Path) -> bool:
        """True for private state: not to be read, listed, searched or written."""
        for part in (real, *real.parents):
            if part in self.hidden:
                return True
            if part.parent == self.install_root and _private_name(part.name):
                return True
        return False

    def check(self, real: Path, *, writing: bool) -> None:
        """Refuse `real` if it is hers, or if it is read-only and this is a write."""
        if self.hides(real):
            raise PathRefused(Code.path_protected, _HIDDEN)
        if writing and any(real == path or path in real.parents for path in self.read_only):
            raise PathRefused(Code.path_protected, _READ_ONLY_REFUSAL)


def protected_for(settings: Settings) -> Protected:
    """The protected set for this installation and its configured data locations."""

    return Protected.for_install(
        INSTALL_ROOT,
        [
            anchored(settings.data_dir),
            anchored(settings.memory.db_path).parent,
            anchored(settings.voice.voices_dir),
            anchored(settings.telemetry.dir),
            anchored(settings.realtime.metrics_path).parent,
        ],
    )


def _private_name(name: str) -> bool:
    # Case folded on every system: on Windows "claude.md" is the same file, and
    # elsewhere hiding a namesake too costs nothing.
    folded = name.casefold()
    return name.startswith(".") or any(
        fnmatch.fnmatchcase(folded, local.casefold()) for local in LOCAL_ONLY
    )
