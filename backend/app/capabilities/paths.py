"""Path containment: the boundary that decides which files Veronica may touch.

Every `files.*` capability goes through `resolve_within`. It is kept as one function
in one module, with its own test file, because a hole here exposes every file on the
machine rather than just the allowed folders.

Known, accepted limitation: the check happens at resolve time. A symlink swapped
between the check and the open (TOCTOU) is not defended against. For a single-user
local agent that is an acceptable residual risk.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from app.kernel.errors import Code

# Windows device names: opening one talks to hardware, not a file (COM1 can block).
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class PathRefused(Exception):
    """A path was refused: nothing was read, listed or written there."""

    def __init__(self, code: Code, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def resolve_roots(roots: Sequence[str]) -> list[Path]:
    """Resolve configured roots once at startup, dropping any that don't exist."""
    resolved: list[Path] = []
    for root in roots:
        try:
            candidate = Path(root).resolve(strict=True)
        except OSError:
            continue  # a missing root is a config problem, not a crash
        if candidate.is_dir():
            resolved.append(candidate)
    return resolved


def resolve_within(
    candidate: str, roots: Sequence[Path], hidden: Callable[[Path], bool] | None = None
) -> Path:
    """Return the real path if it is inside an allow-listed root, else refuse.

    Order matters: reject the shapes that would make containment meaningless
    before comparing, then compare against fully-resolved roots. `hidden` says what is
    hers alone (`protected.py`), which a relative path is never found in.
    """
    if not candidate or not candidate.strip():
        raise PathRefused(Code.path_invalid, "I need a file path.")
    if not roots:
        raise PathRefused(Code.path_outside_roots, "I'm not allowed to open any folders yet.")

    raw = Path(candidate)
    if not raw.is_absolute():
        raw = _in_a_root(raw, roots, hidden)

    # `resolve` both collapses `..` and follows symlinks/junctions, so traversal and
    # symlink escape are handled in one step.
    real = raw.resolve(strict=False)

    if real.drive.startswith("\\\\"):
        # \\server\share (network) or \\?\C: (device namespace): both can reach
        # outside the allow-list in ways the containment check cannot reason about.
        raise PathRefused(Code.path_invalid, "I can't open network or device paths.")

    tail = str(real)[len(real.drive) :]
    if ":" in tail:
        # An alternate data stream (file.txt:hidden) survives resolve() and would be a
        # side channel past the extension/containment checks.
        raise PathRefused(Code.path_invalid, "That isn't a plain file path.")

    if _reserved(real):
        raise PathRefused(Code.path_invalid, "That's a reserved device name, not a file.")

    # Win32 drops the trailing dots and spaces of a name when it opens or creates it:
    # "CLAUDE.md." is written as CLAUDE.md, past every check here and in `protected.py`,
    # which compare the name as it was given. No Windows file can be named so, so none
    # is taken; elsewhere such a name is a file of its own.
    if sys.platform == "win32" and any(part.endswith((".", " ")) for part in real.parts[1:]):
        raise PathRefused(Code.path_invalid, "That isn't a plain file path.")

    if _inside(real, roots):
        return real

    raise PathRefused(
        Code.path_outside_roots, "That file is outside the folders I'm allowed to open."
    )


def _in_a_root(
    relative: Path, roots: Sequence[Path], hidden: Callable[[Path], bool] | None
) -> Path:
    """A relative path, read inside the roots: the root that holds it, else the root that
    holds its folder (a new file goes where its folder is), else the first root.
    Relative to the server's working directory, it named a place the user never sees,
    so it was refused, and the model tried "notes/shopping.md" three times (bug 29).

    Two roots holding it are asked about, not guessed between. Whether it exists is
    looked up before the containment check, so only what lies inside the roots, and
    isn't hers alone, is counted or named; and nothing that climbs out ("..") or names
    a drive or a drive's top is taken. Otherwise the answer would tell of other files.
    """
    if relative.drive or relative.root or ".." in relative.parts:
        raise PathRefused(Code.path_invalid, "I need the full path to that file.")
    if _reserved(relative):  # a device answers in every folder: not looked up
        raise PathRefused(Code.path_invalid, "That's a reserved device name, not a file.")
    held = _holding(relative, roots, hidden)
    if not held and len(relative.parts) > 1:
        held = [folder / relative.name for folder in _holding(relative.parent, roots, hidden)]
    if len(held) > 1:
        which = ", ".join(str(path) for path in held)
        raise PathRefused(
            Code.path_invalid, f"That could be in more than one of my folders: {which}."
        )
    return held[0] if held else roots[0] / relative


def _holding(
    relative: Path, roots: Sequence[Path], hidden: Callable[[Path], bool] | None
) -> list[Path]:
    """Each root's `relative` that exists, once for each real place: through a link it
    may lead outside the roots, or into what is hers alone, and is then left out."""
    held: dict[Path, Path] = {}
    for root in roots:
        joined = root / relative
        real = joined.resolve(strict=False)
        if not _inside(real, roots) or (hidden is not None and hidden(real)):
            continue
        if real not in held and joined.exists():
            held[real] = joined
    return list(held.values())


def _inside(real: Path, roots: Sequence[Path]) -> bool:
    # Windows paths compare case-insensitively, so a differently-cased root still
    # matches without a manual normcase.
    return any(real == root or root in real.parents for root in roots)


def _reserved(path: Path) -> bool:
    # Win32 strips trailing dots and spaces before looking up the name, so "CON " and
    # "CON." reach the same device as a bare "CON"; a plain `stem` comparison would
    # miss both.
    return path.stem.rstrip(" .").upper() in _RESERVED
