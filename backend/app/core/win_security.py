"""The Windows security API, for `data_acl.py`: whose SID this process runs as, a
file's owner and DACL read and written in SDDL, and whether a volume stores
permissions at all.

ctypes bindings, declared on libraries loaded for this module alone, so the argument
types set here never change `ctypes.windll` for the rest of the process. SDDL rather
than icacls: icacls prints account names in the system's language and code page, and
looks up every account it is given, while SDDL names every entry by SID or a fixed
alias (SY, BA, WD) on every Windows and needs no lookup. Paths go to the API in their
`\\\\?\\` form, which reaches past 260 characters.
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

# One entry of a DACL in SDDL: (type;flags;rights;object;inherited object;SID…).
_ENTRY = re.compile(r"\((\w+);([^;]*);[^;]*;[^;]*;[^;]*;([^;)]+)")
_OWNER, _DACL = 0x1, 0x4  # OWNER_ and DACL_SECURITY_INFORMATION
_FILE = 1  # SE_FILE_OBJECT


class DataAclError(RuntimeError):
    """The folder's permissions could not be read or set."""


class Security(NamedTuple):
    """A file's owner (an SDDL SID or alias) and DACL: its flags ("PAI") and its entries
    as (type, flags, SID)."""

    owner: str
    head: str
    entries: list[tuple[str, str, str]]


def _failed(what: str) -> DataAclError:
    return DataAclError(f"{what} (error {ctypes.get_last_error()})")


@cache
def _win32() -> tuple[Any, Any]:
    """advapi32 and kernel32 loaded for this module alone, so the argument types declared
    here never change `ctypes.windll` for the rest of the process."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, dword, text = wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p
    pointer = ctypes.c_void_p
    signatures: list[tuple[Any, str, list[Any], Any]] = [
        (kernel, "GetCurrentProcess", [], handle),
        (kernel, "CloseHandle", [handle], wintypes.BOOL),
        (kernel, "LocalFree", [pointer], pointer),
        (kernel, "GetVolumePathNameW", [text, text, dword], wintypes.BOOL),
        (kernel, "GetDriveTypeW", [text], wintypes.UINT),
        (
            kernel,
            "GetVolumeInformationW",
            [text, text, dword, pointer, pointer, ctypes.POINTER(dword), text, dword],
            wintypes.BOOL,
        ),
        (advapi, "OpenProcessToken", [handle, dword, ctypes.POINTER(handle)], wintypes.BOOL),
        (
            advapi,
            "GetTokenInformation",
            [handle, ctypes.c_int, pointer, dword, ctypes.POINTER(dword)],
            wintypes.BOOL,
        ),
        (advapi, "ConvertSidToStringSidW", [pointer, ctypes.POINTER(text)], wintypes.BOOL),
        (advapi, "ConvertStringSidToSidW", [text, ctypes.POINTER(pointer)], wintypes.BOOL),
        (
            advapi,
            "GetNamedSecurityInfoW",
            [
                text,
                ctypes.c_int,
                dword,
                pointer,
                pointer,
                pointer,
                pointer,
                ctypes.POINTER(pointer),
            ],
            dword,
        ),
        (
            advapi,
            "ConvertSecurityDescriptorToStringSecurityDescriptorW",
            [pointer, dword, dword, ctypes.POINTER(text), pointer],
            wintypes.BOOL,
        ),
        (
            advapi,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            [text, dword, ctypes.POINTER(pointer), pointer],
            wintypes.BOOL,
        ),
        (
            advapi,
            "GetSecurityDescriptorDacl",
            [
                pointer,
                ctypes.POINTER(wintypes.BOOL),
                ctypes.POINTER(pointer),
                ctypes.POINTER(wintypes.BOOL),
            ],
            wintypes.BOOL,
        ),
        (
            advapi,
            "SetNamedSecurityInfoW",
            [text, ctypes.c_int, dword, pointer, pointer, pointer, pointer],
            dword,
        ),
    ]
    for library, name, argtypes, restype in signatures:
        function = getattr(library, name)
        function.argtypes, function.restype = argtypes, restype
    return advapi, kernel


@cache  # a process's account doesn't change, and a check asks once per entry
def current_user_sid() -> str:
    """The SID of the running process's token, as a string: not a user name, which can
    be localised or belong to the console session rather than the process."""
    if sys.platform != "win32":
        raise DataAclError("not on Windows")
    advapi, kernel = _win32()
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise _failed("could not open the process token")
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise _failed("could not read the token's user")
        # TOKEN_USER opens with a SID_AND_ATTRIBUTES, whose first member is the SID.
        return _sid_text(ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents)
    finally:
        kernel.CloseHandle(token)


def _sid_text(sid: Any) -> str:
    advapi, kernel = _win32()
    text = ctypes.c_wchar_p()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise _failed("could not convert a SID")
    try:
        return str(text.value)
    finally:
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))


@cache  # a handful of aliases, asked for at every entry
def full_sid(name: str) -> str:
    """An SDDL SID or alias ("WD", "AU") as a full SID string, so the two compare."""
    advapi, kernel = _win32()
    sid = ctypes.c_void_p()
    if not advapi.ConvertStringSidToSidW(name, ctypes.byref(sid)):
        raise _failed("could not read a SID")
    try:
        return _sid_text(sid)
    finally:
        kernel.LocalFree(sid)


def security(path: Path) -> Security:
    """`path`'s owner and DACL, in SDDL."""
    advapi, kernel = _win32()
    descriptor = ctypes.c_void_p()
    error = advapi.GetNamedSecurityInfoW(
        native(path), _FILE, _OWNER | _DACL, None, None, None, None, ctypes.byref(descriptor)
    )
    if error:
        raise DataAclError(f"could not read the permissions (error {error})")
    try:
        text = ctypes.c_wchar_p()
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, _OWNER | _DACL, ctypes.byref(text), None
        ):
            raise _failed("could not convert the permissions")
        try:
            sddl = str(text.value)  # "O:<owner>D:<flags>(<entry>)…"; an alias holds no ":"
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.LocalFree(descriptor)
    owner, _, text_dacl = sddl.removeprefix("O:").partition("D:")
    head = text_dacl.split("(", 1)[0]
    entries = [(kind, flags, sid) for kind, flags, sid in _ENTRY.findall(text_dacl)]
    return Security(owner, head, entries)


def set_owner(path: Path, sid: str) -> None:
    """Make `sid` the owner of `path`: an owner can always change the permissions, so one
    that isn't hers could open them again after every repair."""
    advapi, kernel = _win32()
    owner = ctypes.c_void_p()
    if not advapi.ConvertStringSidToSidW(sid, ctypes.byref(owner)):
        raise _failed("could not read a SID")
    try:
        error = advapi.SetNamedSecurityInfoW(native(path), _FILE, _OWNER, owner, None, None, None)
        if error:
            raise DataAclError(f"could not take ownership (error {error})")
    finally:
        kernel.LocalFree(owner)


def set_dacl(path: Path, entries: str, *, protected: bool) -> None:
    """Replace `path`'s own entries with `entries` (SDDL: "(A;OICI;FA;;;SY)…", or "" for
    none), inheritance off when `protected`, on otherwise. Windows carries inheritable
    entries down to what the folder holds. Nothing here looks an account up, so a SID
    that maps to no account (deleted, or from another machine) can't make it fail."""
    advapi, kernel = _win32()
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"D:{entries}", 1, ctypes.byref(descriptor), None
    ):
        raise _failed("could not build the permissions")
    try:
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        advapi.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
        )
        if not present or not acl:  # a missing DACL would give everyone full control
            raise DataAclError("the permissions built are empty")
        # With PROTECTED_ or UNPROTECTED_DACL_SECURITY_INFORMATION.
        flags = _DACL | (0x80000000 if protected else 0x20000000)
        error = advapi.SetNamedSecurityInfoW(native(path), _FILE, flags, None, None, acl, None)
        if error:
            raise DataAclError(f"could not set the permissions (error {error})")
    finally:
        kernel.LocalFree(descriptor)


def keeps_permissions(path: Path) -> bool:
    """Whether `path` is on a local volume that stores permissions: on a network share
    the grant would name an account the server doesn't know, and FAT has none."""
    _advapi, kernel = _win32()
    root = ctypes.create_unicode_buffer(1024)
    if not kernel.GetVolumePathNameW(str(path), root, len(root)):
        return False
    if kernel.GetDriveTypeW(root.value) == 4:  # DRIVE_REMOTE
        return False
    flags = wintypes.DWORD()
    if not kernel.GetVolumeInformationW(
        root.value, None, 0, None, None, ctypes.byref(flags), None, 0
    ):
        return False
    return bool(flags.value & 0x8)  # FILE_PERSISTENT_ACLS


_EXTENDED = "\\\\?\\"  # the extended-length prefix: two backslashes, "?", a backslash


def native(path: Path) -> str:
    """`path` with the extended-length prefix, which the API and `os` read past 260
    characters (a share's path takes it as "UNC" in place of its leading pair)."""
    text = str(path)
    if not path.is_absolute() or text.startswith(_EXTENDED):
        return text
    if text.startswith("\\\\"):
        return _EXTENDED + "UNC\\" + text[2:]
    return _EXTENDED + text
