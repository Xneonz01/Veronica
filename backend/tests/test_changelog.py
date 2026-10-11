"""The changelog loader refuses a malformed file instead of guessing."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.changelog import ChangelogError, load_changelog

_GOOD = """
- version: "1.2.0"
  label: "Something"
  date: 2026-01-02
  channel: dev
  sections:
    new: ["A thing."]
    fixed: ["A bug."]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "CHANGELOG.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_well_formed_entry_loads_with_sections_in_order(tmp_path: Path) -> None:
    [entry] = load_changelog(_write(tmp_path, _GOOD))
    assert entry.version == "1.2.0"
    assert entry.date == "2026-01-02"  # kept as written, not turned into a date object
    assert list(entry.sections) == ["new", "fixed"]
    assert entry.as_dict()["sections"]["fixed"] == ["A bug."]


def test_a_duplicate_section_is_refused_rather_than_silently_merged(tmp_path: Path) -> None:
    text = _GOOD + '    fixed: ["Another bug."]\n'
    with pytest.raises(ChangelogError, match="duplicate key"):
        load_changelog(_write(tmp_path, text))


def test_an_unknown_section_is_refused(tmp_path: Path) -> None:
    text = _GOOD.replace("fixed:", "fixes:")
    with pytest.raises(ChangelogError, match="unknown sections"):
        load_changelog(_write(tmp_path, text))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('"1.2.0"', '"1.2"', "MAJOR.MINOR.PATCH"),
        ("2026-01-02", '"January 2"', "YYYY-MM-DD"),
        ("channel: dev", "channel: nightly", "channel"),
        ('label: "Something"', 'label: ""', "label is empty"),
    ],
)
def test_bad_identity_fields_are_refused(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ChangelogError, match=message):
        load_changelog(_write(tmp_path, _GOOD.replace(old, new)))


def test_an_empty_or_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ChangelogError):
        load_changelog(_write(tmp_path, ""))
    with pytest.raises(ChangelogError, match="cannot read"):
        load_changelog(tmp_path / "missing.yaml")
