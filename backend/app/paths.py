"""Where Veronica's files are: the installation she runs from, and the relative paths of
her settings anchored to it, so her data doesn't move with the folder she was started
from. One place, where every module used to count `parents[N]` to find it. Imports
nothing of the app's, so any module can use it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator

# backend/app/paths.py: the installation, the repository's root, is three levels up.
INSTALL_ROOT = Path(__file__).resolve().parents[2]
# The same folder before links are followed: the two differ behind a junction, and what
# hides it in her diagnostics must know both (`telemetry/redact.py`).
INSTALL_ROOT_AS_LOADED = Path(__file__).parents[2]


def anchored(value: str | Path) -> Path:
    """A path from a setting: absolute as given, relative under the installation."""
    path = Path(value)
    return path if path.is_absolute() else INSTALL_ROOT / path


def own_place(value: str) -> str:
    """A setting that says where her data is kept, stripped. Refused when it hangs off
    whatever drive or folder the process is in ("\\data", "D:data"), or names the
    installation or a folder above it ("", ".", "..", "data/.."): there her files would
    sit among the code, or beside it."""
    stripped = value.strip()
    path = Path(stripped)
    if (path.drive or path.root) and not path.is_absolute():
        raise ValueError("must be relative to the installation, or absolute")
    if anchored(path).resolve() in (INSTALL_ROOT, *INSTALL_ROOT.parents):
        raise ValueError("must be a place of its own, not the installation")
    return stripped


# The type of every such setting: `data_dir`, and each store's own location.
Place = Annotated[str, AfterValidator(own_place)]
