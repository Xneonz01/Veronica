"""The version is defined once and every place that states it agrees.

`core/version.py` is the source. The newest changelog entry, the Python package,
the npm package and lock file, and the Tauri crate, lock file and bundle config must
all carry the same version, so a release can't ship with a stale number anywhere.
"""

from __future__ import annotations

import json
import tomllib
from itertools import pairwise
from pathlib import Path

import app
from app.core.changelog import SECTIONS, latest, load_changelog
from app.core.version import APP_CHANNEL, APP_VERSION, APP_VERSION_LABEL

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
TAURI = FRONTEND / "src-tauri"


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def test_the_newest_changelog_entry_is_the_running_version() -> None:
    entry = latest()
    assert entry.version == APP_VERSION
    assert entry.label == APP_VERSION_LABEL
    assert entry.channel == APP_CHANNEL


def test_changelog_versions_are_strictly_descending() -> None:
    versions = [_parts(entry.version) for entry in load_changelog()]
    for newer, older in pairwise(versions):
        assert newer > older, f"{newer} is listed above {older}"


def test_changelog_dates_never_go_forward_down_the_file() -> None:
    dates = [entry.date for entry in load_changelog()]
    for newer, older in pairwise(dates):
        assert newer >= older, f"{newer} is listed above {older}"


def test_every_changelog_entry_says_something() -> None:
    for entry in load_changelog():
        assert any(entry.sections.values()), f"{entry.version} has no notes"
        for name, items in entry.sections.items():
            assert name in SECTIONS
            assert all(item for item in items), f"{entry.version}/{name} has an empty note"


def test_the_python_package_version_matches() -> None:
    # `app.__version__` was a literal "0.1.0" that nothing checked while the app moved on.
    assert app.__version__ == APP_VERSION
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == APP_VERSION
    lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text(encoding="utf-8"))
    own = [p for p in lock["package"] if p["name"] == pyproject["project"]["name"]]
    assert own and own[0]["version"] == APP_VERSION


def test_the_npm_package_version_matches() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == APP_VERSION
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == APP_VERSION
    assert lock["packages"][""]["version"] == APP_VERSION


def test_the_tauri_versions_match() -> None:
    cargo = tomllib.loads((TAURI / "Cargo.toml").read_text(encoding="utf-8"))
    assert cargo["package"]["version"] == APP_VERSION
    lock = tomllib.loads((TAURI / "Cargo.lock").read_text(encoding="utf-8"))
    own = [p for p in lock["package"] if p["name"] == cargo["package"]["name"]]
    assert own and own[0]["version"] == APP_VERSION
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["version"] == APP_VERSION
