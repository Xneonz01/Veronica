"""Her data folder is private to the account she runs as.

Real permissions on temporary folders, read through the Win32 API and set with
`icacls`: a check that only passes against a fake proves nothing about the folder on
disk. Windows only, like the check itself; what a start does with each outcome is in
`test_data_dir.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.core import data_acl, win_security

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")

_EVERYONE = "S-1-1-0"
_AUTHENTICATED_USERS = "S-1-5-11"
_INTERACTIVE = "S-1-5-4"
_USERS = "S-1-5-32-545"
_GONE = "S-1-5-21-1000-2000-3000-1001"  # made up: no account, like a deleted one


def _icacls(path: Path, *args: str) -> None:
    subprocess.run(["icacls", str(path), *args, "/q"], check=True, capture_output=True)


def _grant(path: Path, sid: str, rights: str = "M") -> None:
    _icacls(path, "/grant", f"*{sid}:(OI)(CI){rights}")


@pytest.mark.parametrize("sid", [_AUTHENTICATED_USERS, _EVERYONE, _USERS, _INTERACTIVE])
def test_a_grant_to_anyone_else_is_found_and_removed(tmp_path: Path, sid: str) -> None:
    """Authenticated Users is what most installs give a folder; any other account or
    group counts the same, whatever the system's language calls it (mutation: allow
    one of them)."""
    data = tmp_path / "data"
    assert data_acl.ensure(data) == "created"
    _grant(data, sid)
    assert not data_acl.is_private(data)

    assert data_acl.ensure(data) == "repaired"

    assert data_acl.is_private(data)
    (data / "memory.db").write_text("still mine", encoding="utf-8")  # the owner keeps access
    assert data_acl.ensure(data) == "ok"


def test_an_inheriting_top_is_not_private(tmp_path: Path) -> None:
    """With inheritance on, a change to a folder above reaches inside, even while what
    comes down is private (mutation: drop the check for a protected top)."""
    above = tmp_path / "above"
    data_acl.ensure(above)
    data = above / "data"
    data.mkdir()

    assert data_acl.is_private(data, top=False)
    assert not data_acl.is_private(data)
    assert data_acl.ensure(data) == "repaired"


def test_the_root_of_any_drive_is_too_wide() -> None:
    """Not only the drives that hold the installation or the home folder (mutation:
    drop the check for a root)."""
    free = next((f"{c}:\\" for c in "ZYXWVUTSRQPONMLKJ" if not Path(f"{c}:\\").exists()), None)
    if free is None:
        pytest.skip("every drive letter is in use")

    assert data_acl.too_wide(Path(free))


def test_an_explicit_grant_inside_the_folder_is_cleared_too(tmp_path: Path) -> None:
    """Inheritance brings the top's entries down, but an explicit grant on a folder
    inside keeps it, under a top that is private itself (mutation: stop checking the
    folders inside)."""
    data = tmp_path / "data"
    data_acl.ensure(data)
    voices = data / "voices"
    voices.mkdir()
    _grant(voices, _EVERYONE, "R")

    assert data_acl.ensure(data) == "repaired"

    assert data_acl.is_private(voices, top=False)


def test_a_folder_inside_that_inherits_nothing_stays_its_owners(tmp_path: Path) -> None:
    """Inheritance off and only Authenticated Users granted: removing that name left no
    entry at all and locked the owner out. Reset, it takes the top's entries (mutation:
    remove the other names instead)."""
    data = tmp_path / "data"
    voices = data / "voices"
    voices.mkdir(parents=True)
    (voices / "clip.wav").write_bytes(b"RIFF")
    _icacls(voices, "/inheritance:r", "/grant:r", f"*{_AUTHENTICATED_USERS}:(OI)(CI)M")

    assert data_acl.ensure(data) == "repaired"

    assert data_acl.is_private(voices, top=False)
    assert data_acl.is_private(voices / "clip.wav", top=False)
    assert (voices / "clip.wav").read_bytes() == b"RIFF"
    (voices / "new.wav").write_bytes(b"RIFF")
    assert sorted(path.name for path in voices.iterdir()) == ["clip.wav", "new.wav"]


def test_a_junction_inside_is_not_followed(tmp_path: Path) -> None:
    """A junction points at things that are not hers; the old recursive pass stripped
    a grant from a file it led to (mutation: walk through links, as `os.walk` does)."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "notes.txt").write_text("someone else's", encoding="utf-8")
    _grant(shared, _EVERYONE, "R")
    _icacls(shared / "notes.txt", "/grant", f"*{_INTERACTIVE}:R")  # its own, not inherited
    before = win_security.security(shared), win_security.security(shared / "notes.txt")
    data = tmp_path / "data"
    data.mkdir()
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(data / "linked"), str(shared)],
        check=True,
        capture_output=True,
    )

    assert data_acl.ensure(data) == "repaired"

    assert (win_security.security(shared), win_security.security(shared / "notes.txt")) == before


def test_an_account_that_no_longer_exists_is_removed_too(tmp_path: Path) -> None:
    """icacls looks up every account it is given, and one that maps to nothing made the
    whole repair fail: a start under a profile whose folders name deleted accounts never
    made the folder private. Inherited here, then the folder's own and inside it."""
    user = win_security.current_user_sid()
    above = tmp_path / "above"
    above.mkdir()
    win_security.set_dacl(
        above, f"(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)(A;OICI;FR;;;{_GONE})", protected=True
    )
    data = above / "data"

    assert data_acl.ensure(data) == "created"
    assert data_acl.is_private(data)

    voices = data / "voices"
    voices.mkdir()
    win_security.set_dacl(voices, f"(A;OICI;FR;;;{_GONE})", protected=False)
    win_security.set_dacl(data, f"(A;OICI;FA;;;{user})(A;OICI;FR;;;{_GONE})", protected=True)

    assert data_acl.ensure(data) == "repaired"
    assert data_acl.is_private(data)
    assert data_acl.is_private(voices, top=False)


def test_permissions_that_would_open_the_folder_to_everyone_are_never_set(
    tmp_path: Path,
) -> None:
    """A missing DACL gives everyone full control (mutation: drop the check)."""
    before = win_security.security(tmp_path)

    with pytest.raises(win_security.DataAclError):
        win_security.set_dacl(tmp_path, "NO_ACCESS_CONTROL", protected=True)

    assert win_security.security(tmp_path) == before


def test_a_folder_named_outside_ascii_is_read(tmp_path: Path) -> None:
    """icacls writes in the console's code page: an accented name crashed the start."""
    data = tmp_path / "Café Ünïcode" / "données"

    assert data_acl.ensure(data) == "created"
    _grant(data, _AUTHENTICATED_USERS)
    assert data_acl.ensure(data) == "repaired"
    assert data_acl.is_private(data)


def test_a_folder_with_no_permissions_at_all_is_open_to_everyone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A null DACL grants everything to everyone (mutation: read no entries as none
    granted)."""
    user = win_security.current_user_sid()
    found = win_security.Security(user, "PNO_ACCESS_CONTROL", [])
    monkeypatch.setattr(data_acl, "security", lambda _path: found)

    assert not data_acl.is_private(tmp_path)


def test_another_account_owning_an_entry_is_not_private(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An owner can always change the permissions again (mutation: ignore the owner)."""
    grants = [("A", "OICIID", win_security.current_user_sid())]
    found = win_security.Security(_GONE, "AI", grants)
    monkeypatch.setattr(data_acl, "security", lambda _path: found)

    assert not data_acl.is_private(tmp_path, top=False)


def test_a_top_that_shuts_her_out_is_not_private(tmp_path: Path) -> None:
    """Only SYSTEM and Administrators granted: private to no use (mutation: don't ask for
    her own entry)."""
    data = tmp_path / "data"
    data.mkdir()
    win_security.set_dacl(data, "(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)", protected=True)

    assert not data_acl.is_private(data)
    assert data_acl.ensure(data) == "repaired"
    (data / "notes.txt").write_text("hers again", encoding="utf-8")


def test_the_whole_tree_is_checked_not_two_levels(tmp_path: Path) -> None:
    """A file at the top, or deep inside, that keeps a grant of its own (a restore, a
    move) was never looked at while the folders were private (mutation: check the top
    and the folders in it only)."""
    data = tmp_path / "data"
    data_acl.ensure(data)
    (data / "memory").mkdir()
    for inside in (data / "persona.json", data / "memory" / "memory.db"):
        inside.write_text("hers", encoding="utf-8")
        _icacls(inside, "/grant", f"*{_EVERYONE}:R")

    assert data_acl.ensure(data) == "repaired"

    assert data_acl.is_private(data / "persona.json", top=False)
    assert data_acl.is_private(data / "memory" / "memory.db", top=False)


def test_an_entry_it_cannot_change_fails_every_start_until_it_can(tmp_path: Path) -> None:
    """The repair stopped at the first entry it couldn't change, and the next start,
    finding the top private, said "ok" (mutation: stop at the first failure and check
    only the top)."""
    data = tmp_path / "data"
    data_acl.ensure(data)
    stuck = data / "a-stuck.txt"
    stuck.write_text("hers", encoding="utf-8")
    # Her owner's rights narrowed to reading: she can't change its permissions.
    win_security.set_dacl(stuck, f"(A;;FR;;;{_EVERYONE})(A;;FR;;;OW)", protected=True)
    (data / "b-open.txt").write_text("hers", encoding="utf-8")
    _icacls(data / "b-open.txt", "/grant", f"*{_EVERYONE}:R")

    for _start in range(2):
        with pytest.raises(win_security.DataAclError):
            data_acl.ensure(data)
    assert data_acl.is_private(data / "b-open.txt", top=False)  # the rest was still repaired


def test_a_folder_it_cannot_list_is_a_failure_not_a_skip(tmp_path: Path) -> None:
    """(mutation: skip a folder that can't be listed)"""
    data = tmp_path / "data"
    data_acl.ensure(data)
    closed = data / "closed"
    closed.mkdir()
    (closed / "clip.wav").write_bytes(b"RIFF")
    win_security.set_dacl(closed, "(A;OICI;FA;;;SY)", protected=True)

    try:
        with pytest.raises(win_security.DataAclError):
            data_acl.ensure(data)
    finally:
        # Opened again, or pytest can't remove it later. The owner may always rewrite
        # the entries of what it owns.
        me = win_security.current_user_sid()
        win_security.set_dacl(closed, f"(A;OICI;FA;;;{me})", protected=True)


def _own_entries(path: Path) -> list[tuple[str, str, str]]:
    return [entry for entry in win_security.security(path).entries if "ID" not in entry[1]]


def test_a_file_with_a_second_name_elsewhere_keeps_its_own_entries(tmp_path: Path) -> None:
    """A hard link has one set of permissions for both names, so resetting it inside the
    folder stripped the grants it has outside (mutation: walk into it). What it inherits
    follows whichever folder last passed its entries down: Windows does that, not her."""
    outside = tmp_path / "shared.txt"
    outside.write_text("someone else's", encoding="utf-8")
    _icacls(outside, "/grant", f"*{_INTERACTIVE}:R")
    before = _own_entries(outside)
    data = tmp_path / "data"
    data.mkdir()
    (data / "linked.txt").hardlink_to(outside)

    assert data_acl.ensure(data) == "repaired"

    assert _own_entries(outside) == before != []


def test_a_path_past_260_characters_is_read_and_repaired(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data_acl.ensure(data)
    deep = Path(win_security.native(data / ("folder-" * 20) / ("nested-" * 20)))
    deep.mkdir(parents=True)
    (deep / "clip.wav").write_bytes(b"RIFF")
    win_security.set_dacl(deep / "clip.wav", f"(A;;FR;;;{_EVERYONE})", protected=False)
    assert len(str(deep / "clip.wav")) > 260

    assert data_acl.ensure(data) == "repaired"

    assert data_acl.is_private(deep / "clip.wav", top=False)


def test_folders_other_programs_keep_their_things_in_are_too_wide() -> None:
    """Locked as her data folder, their other owners' entries would go for good
    (mutation: guard only the home folder and the installation)."""
    local = Path(os.environ["LOCALAPPDATA"])
    for shared in (local, Path(os.environ["APPDATA"]), Path.home() / "Documents"):
        assert data_acl.too_wide(shared)
    assert not data_acl.too_wide(local / "Veronica")


def test_a_drive_that_keeps_no_permissions_is_left_as_it_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A network share or FAT: the grant would name an account the server doesn't know,
    and turning inheritance off would drop the entries that let the owner in."""
    data = tmp_path / "data"
    data.mkdir()
    _grant(data, _AUTHENTICATED_USERS)
    before = win_security.security(data)
    monkeypatch.setattr(data_acl, "keeps_permissions", lambda _path: False)

    assert data_acl.ensure(data) == "unsupported"

    assert win_security.security(data) == before


def test_a_local_ntfs_folder_keeps_permissions(tmp_path: Path) -> None:
    assert win_security.keeps_permissions(tmp_path)


def test_the_owner_is_named_by_sid_not_by_name() -> None:
    """A local, domain or work (Entra ID, S-1-12-1-…) account: never a name, which can be
    localised."""
    assert re.fullmatch(r"S-1-\d+(-\d+)+", win_security.current_user_sid())


def test_an_alias_is_read_as_its_sid() -> None:
    """SDDL writes some SIDs as an alias; the check compares full SIDs."""
    assert win_security.full_sid("SY") == "S-1-5-18"
    assert win_security.full_sid("BA") == "S-1-5-32-544"
