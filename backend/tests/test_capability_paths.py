"""Path containment: which files the file capabilities are allowed to touch.

A gap here would expose every file on the machine instead of the configured roots,
so each known escape shape gets its own case.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.capabilities.paths import PathRefused, resolve_roots, resolve_within
from app.kernel.errors import Code


@pytest.fixture
def roots(tmp_path: Path) -> list[Path]:
    allowed = tmp_path / "allowed"
    (allowed / "sub").mkdir(parents=True)
    (allowed / "sub" / "note.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "keys.txt").write_text("hunter2", encoding="utf-8")
    return [allowed.resolve()]


def test_a_path_inside_the_root_is_allowed(roots: list[Path]) -> None:
    resolved = resolve_within(str(roots[0] / "sub" / "note.txt"), roots)
    assert resolved.name == "note.txt"


def test_traversal_out_of_the_root_is_refused(roots: list[Path]) -> None:
    escape = str(roots[0] / ".." / "secret" / "keys.txt")
    with pytest.raises(PathRefused) as exc:
        resolve_within(escape, roots)
    assert exc.value.code is Code.path_outside_roots


def test_an_absolute_path_outside_the_root_is_refused(roots: list[Path]) -> None:
    outside = str(roots[0].parent / "secret" / "keys.txt")
    with pytest.raises(PathRefused) as exc:
        resolve_within(outside, roots)
    assert exc.value.code is Code.path_outside_roots


def test_a_symlink_escaping_the_root_is_refused(roots: list[Path], tmp_path: Path) -> None:
    # Creating a symlink on Windows needs Developer Mode or admin; skip rather than
    # fail confusingly on a machine that doesn't have it.
    link = roots[0] / "escape"
    try:
        os.symlink(tmp_path / "secret", link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this machine")
    with pytest.raises(PathRefused) as exc:
        resolve_within(str(link / "keys.txt"), roots)
    assert exc.value.code is Code.path_outside_roots


@pytest.mark.skipif(sys.platform != "win32", reason="elsewhere these are plain names")
@pytest.mark.parametrize(
    "candidate",
    [
        r"\\server\share\file.txt",  # UNC — reaches off the machine entirely
        r"\\?\C:\Windows\notepad.exe",  # device namespace bypasses normal parsing
    ],
)
def test_network_and_device_paths_are_refused(roots: list[Path], candidate: str) -> None:
    with pytest.raises(PathRefused) as exc:
        resolve_within(candidate, roots)
    assert exc.value.code is Code.path_invalid


def test_an_alternate_data_stream_is_refused(roots: list[Path]) -> None:
    # `C:\x.txt:hidden` survives resolve() and would be a side channel past the checks.
    with pytest.raises(PathRefused) as exc:
        resolve_within(str(roots[0] / "note.txt") + ":hidden", roots)
    assert exc.value.code is Code.path_invalid


@pytest.mark.parametrize("name", ["CON", "nul", "COM1", "LPT1"])
def test_reserved_device_names_are_refused(roots: list[Path], name: str) -> None:
    with pytest.raises(PathRefused) as exc:
        resolve_within(str(roots[0] / f"{name}.txt"), roots)
    assert exc.value.code is Code.path_invalid


def test_a_relative_path_is_read_in_the_folder_that_holds_it(
    roots: list[Path], tmp_path: Path
) -> None:
    """Bug 29: refused, the model tried "notes/shopping.md" three times; its base was
    the server's working directory, which the user never sees (mutation: the first
    folder whatever holds it)."""
    other = tmp_path / "other"
    (other / "notes").mkdir(parents=True)
    (other / "notes" / "shopping.md").write_text("milk", encoding="utf-8")
    both = [roots[0], other.resolve()]

    assert resolve_within("sub/note.txt", both) == roots[0] / "sub" / "note.txt"
    assert resolve_within("notes/shopping.md", both) == other.resolve() / "notes" / "shopping.md"


def test_a_new_file_goes_in_the_first_folder(roots: list[Path], tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()

    assert resolve_within("ideas.md", [roots[0], other.resolve()]) == roots[0] / "ideas.md"


def test_a_relative_path_two_folders_hold_is_asked_about(roots: list[Path], tmp_path: Path) -> None:
    """Not guessed: the answer would come from a file nobody named (mutation: take the
    first)."""
    other = tmp_path / "other"
    (other / "sub").mkdir(parents=True)
    (other / "sub" / "note.txt").write_text("hello", encoding="utf-8")

    with pytest.raises(PathRefused) as exc:
        resolve_within("sub/note.txt", [roots[0], other.resolve()])

    assert exc.value.code is Code.path_invalid
    assert exc.value.detail.startswith("That could be in more than one of my folders:")
    assert str(roots[0] / "sub" / "note.txt") in exc.value.detail
    assert str(other.resolve() / "sub" / "note.txt") in exc.value.detail


def test_a_new_file_goes_where_its_folder_is(roots: list[Path], tmp_path: Path) -> None:
    """With only a later folder holding notes/, a new notes/todo.md was put in the first
    and refused as a missing folder; two holding it are asked about (mutation: the
    first folder)."""
    other = tmp_path / "other"
    (other / "notes").mkdir(parents=True)
    both = [roots[0], other.resolve()]

    assert resolve_within("notes/todo.md", both) == other.resolve() / "notes" / "todo.md"

    (roots[0] / "notes").mkdir()
    with pytest.raises(PathRefused) as exc:
        resolve_within("notes/todo.md", both)
    assert exc.value.detail.startswith("That could be in more than one of my folders:")


def test_what_is_hers_alone_is_neither_found_nor_named(roots: list[Path], tmp_path: Path) -> None:
    """Her own data folder made the user's one of the same name ambiguous, and was named
    in the refusal (mutation: count what is hidden)."""
    other = tmp_path / "other"
    (other / "sub").mkdir(parents=True)
    both = [roots[0], other.resolve()]
    hers = roots[0] / "sub"

    assert resolve_within("sub", both, lambda real: real == hers) == other.resolve() / "sub"


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is made the Windows way")
def test_a_link_out_of_a_folder_tells_nothing_of_what_is_there(
    roots: list[Path], tmp_path: Path
) -> None:
    """Through a link out of a later folder, a file outside was refused as outside, and a
    missing one taken as a new file: the model could tell which files exist there
    (mutation: count what a link leads out to)."""
    import _winapi  # CPython's own way to make a junction, which needs no privilege

    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "secret" / "inner").mkdir()
    _winapi.CreateJunction(str(tmp_path / "secret"), str(other / "j"))
    both = [roots[0], other.resolve()]

    def outcome(relative: str) -> object:
        """A refusal's code, or whether it was taken as a new file in the first folder."""
        try:
            return resolve_within(relative, both).is_relative_to(roots[0])
        except PathRefused as exc:
            return exc.code

    assert (tmp_path / "secret" / "keys.txt").exists()
    assert outcome("j/keys.txt") == outcome("j/nothing.txt") is True
    # A folder there, through the folder a new file would go in.
    assert outcome("j/inner/new.txt") == outcome("j/nowhere/new.txt") is True


def test_a_device_name_is_refused_before_it_is_looked_up(roots: list[Path], tmp_path: Path) -> None:
    """NUL answers in every folder, so it read as held by two (mutation: look it up
    first)."""
    other = tmp_path / "other"
    other.mkdir()

    with pytest.raises(PathRefused) as exc:
        resolve_within("NUL", [roots[0], other.resolve()])

    assert exc.value.detail == "That's a reserved device name, not a file."


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drops a name's trailing dots")
@pytest.mark.parametrize("name", ["CLAUDE.md.", "AGENTS.md. .", "note.txt "])
def test_a_name_windows_would_change_is_refused(roots: list[Path], name: str) -> None:
    """Windows writes "CLAUDE.md." as CLAUDE.md, past the check on her own files, which
    compares the name as given (mutation: take trailing dots and spaces)."""
    for candidate in (str(roots[0] / name), name):
        with pytest.raises(PathRefused) as exc:
            resolve_within(candidate, roots)
        assert (exc.value.code, exc.value.detail) == (
            Code.path_invalid,
            "That isn't a plain file path.",
        )


_DRIVE_RELATIVE = ["\\secret\\keys.txt", "C:keys.txt"] if sys.platform == "win32" else []


@pytest.mark.parametrize(
    "candidate", ["../secret/keys.txt", "sub/../../secret/keys.txt", "..", *_DRIVE_RELATIVE]
)
def test_a_relative_path_never_climbs_out_or_names_a_drive(
    roots: list[Path], candidate: str
) -> None:
    """Whether it exists is looked up before containment: two folders holding a path
    outside both would have said so (mutation: allow "..")."""
    with pytest.raises(PathRefused) as exc:
        resolve_within(candidate, roots)
    assert exc.value.code is Code.path_invalid
    assert exc.value.detail == "I need the full path to that file."


@pytest.mark.parametrize("candidate", ["", "   "])
def test_empty_paths_are_refused(roots: list[Path], candidate: str) -> None:
    with pytest.raises(PathRefused):
        resolve_within(candidate, roots)


def test_no_configured_roots_means_nothing_is_allowed(tmp_path: Path) -> None:
    with pytest.raises(PathRefused) as exc:
        resolve_within(str(tmp_path / "x.txt"), [])
    assert exc.value.code is Code.path_outside_roots


def test_case_differences_still_match_the_root(roots: list[Path]) -> None:
    # Windows comparison is case-insensitive; a differently-cased path is the same file.
    mixed = str(roots[0] / "sub" / "note.txt").upper()
    assert resolve_within(mixed, roots).name.lower() == "note.txt"


def test_missing_roots_are_dropped_not_fatal(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    assert resolve_roots([str(real), str(tmp_path / "does-not-exist")]) == [real.resolve()]


@pytest.mark.parametrize("name", ["CON ", "CON.", "NUL ", "com1."])
def test_reserved_device_names_with_trailing_dots_or_spaces(roots: list[Path], name: str) -> None:
    # Win32 strips trailing dots and spaces before resolving the name, so these reach
    # the same device a bare "CON" does; a plain stem comparison would miss them.
    with pytest.raises(PathRefused) as exc:
        resolve_within(str(roots[0] / name), roots)
    assert exc.value.code is Code.path_invalid


def test_an_ordinary_name_that_merely_starts_with_a_device_name_is_fine(
    roots: list[Path],
) -> None:
    # The check must not over-reach: "console.txt" and "nullable.py" are normal files.
    for name in ("console.txt", "nullable.py", "comms.md"):
        assert resolve_within(str(roots[0] / name), roots).name == name
