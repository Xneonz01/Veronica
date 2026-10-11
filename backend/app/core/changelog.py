"""Reader for the repository's `CHANGELOG.yaml` — the patch notes.

The file is written once per release and read in three places: `/health` reports
the current label, `GET /changelog` serves it to the app's About panel, and the
version drift test checks it against `core/version.py`.

The loader is strict on purpose. PyYAML quietly keeps only the last of two
duplicate keys, so a repeated `fixed:` would silently drop notes; an unknown
section name would vanish the same way. Both raise here instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.paths import INSTALL_ROOT

CHANGELOG_PATH = INSTALL_ROOT / "CHANGELOG.yaml"

SECTIONS: tuple[str, ...] = ("new", "improved", "fixed", "known")
CHANNELS = frozenset({"dev", "beta", "stable"})
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEYS = frozenset({"version", "label", "date", "channel", "sections"})


class ChangelogError(ValueError):
    """The changelog file is missing, malformed, or breaks one of its rules."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One released version. `sections` holds only the sections the entry uses."""

    version: str
    label: str
    date: str
    channel: str
    sections: dict[str, list[str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "label": self.label,
            "date": self.date,
            "channel": self.channel,
            "sections": {name: list(items) for name, items in self.sections.items()},
        }


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses duplicate keys instead of keeping the last one."""


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.Node) -> dict[Any, Any]:
    if not isinstance(node, yaml.MappingNode):
        raise ChangelogError(f"expected a mapping at line {node.start_mark.line + 1}")
    seen: set[Any] = set()
    for key_node, _value in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise ChangelogError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen.add(key)
    return dict(loader.construct_mapping(node))


def _construct_date(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    """Keep dates as the strings they were written as (YAML would make them `date`s)."""
    if not isinstance(node, yaml.ScalarNode):
        raise ChangelogError(f"expected a date at line {node.start_mark.line + 1}")
    return str(loader.construct_scalar(node))


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)
_UniqueKeyLoader.add_constructor("tag:yaml.org,2002:timestamp", _construct_date)


def load_changelog(path: Path | None = None) -> list[Entry]:
    """Parse and validate the changelog, newest entry first."""
    source = path or CHANGELOG_PATH
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except OSError as exc:
        raise ChangelogError(f"cannot read {source.name}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ChangelogError(f"{source.name} is not valid YAML: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise ChangelogError(f"{source.name} must be a non-empty list of entries")
    return [_entry(item) for item in raw]


def latest(path: Path | None = None) -> Entry:
    """The entry the running build claims to be (the file is ordered newest first)."""
    return load_changelog(path)[0]


def _entry(item: Any) -> Entry:
    if not isinstance(item, dict):
        raise ChangelogError(f"changelog entry is not a mapping: {item!r}")
    unknown = set(item) - _KEYS
    if unknown:
        raise ChangelogError(f"unknown keys {sorted(unknown)} in entry {item.get('version')!r}")
    version, label, date = (str(item.get(key, "")) for key in ("version", "label", "date"))
    channel = str(item.get("channel", "dev"))
    if not _VERSION.match(version):
        raise ChangelogError(f"version {version!r} is not MAJOR.MINOR.PATCH")
    if not label.strip():
        raise ChangelogError(f"{version}: label is empty")
    if not _DATE.match(date):
        raise ChangelogError(f"{version}: date {date!r} is not YYYY-MM-DD")
    if channel not in CHANNELS:
        raise ChangelogError(f"{version}: channel {channel!r} is not one of {sorted(CHANNELS)}")
    sections = item.get("sections") or {}
    if not isinstance(sections, dict):
        raise ChangelogError(f"{version}: sections must be a mapping")
    bad = set(sections) - set(SECTIONS)
    if bad:
        raise ChangelogError(f"{version}: unknown sections {sorted(bad)}")
    parsed: dict[str, list[str]] = {}
    for name in SECTIONS:
        items = sections.get(name)
        if items is None:
            continue
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ChangelogError(f"{version}/{name}: items must be a list of strings")
        parsed[name] = [i.strip() for i in items]
    return Entry(version=version, label=label, date=date, channel=channel, sections=parsed)
