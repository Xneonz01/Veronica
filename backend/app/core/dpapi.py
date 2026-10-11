"""Windows DPAPI: bytes wrapped so that only the Windows account that wrapped them can
unwrap them again, on this computer (ADR 0001).

`CryptProtectData` and `CryptUnprotectData` for the current user, never prompting. A
blob unwraps only with the entropy it was wrapped with, which sets the caller's blobs
apart from other programs'. Windows keeps the account's own DPAPI keys on the same disk,
protected by the account's password: whoever can sign in to it, or has the disk and
the password, can unwrap. ctypes bindings on a crypt32 loaded for this module alone, as
`win_security.py` does, so the argument types set here never change `ctypes.windll`.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from functools import cache
from typing import Any

_UI_FORBIDDEN = 0x1  # CRYPTPROTECT_UI_FORBIDDEN: fail rather than ask


class DpapiError(OSError):
    """Windows would not wrap or unwrap: another account's blob, a reset profile, a
    blob that isn't one, or no DPAPI at all (not Windows)."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    """Whether this system has DPAPI: Windows only."""
    return sys.platform == "win32"


def protect(data: bytes, entropy: bytes) -> bytes:
    """`data` wrapped for the current Windows account."""
    return _call("CryptProtectData", data, entropy)


def unprotect(blob: bytes, entropy: bytes) -> bytes:
    """What `protect` wrapped, for the account that wrapped it; DpapiError otherwise."""
    return _call("CryptUnprotectData", blob, entropy)


def _call(name: str, data: bytes, entropy: bytes) -> bytes:
    crypt32, kernel32 = _win32()
    source, extra, out = _blob(data), _blob(entropy), _Blob()
    if not getattr(crypt32, name)(
        ctypes.byref(source),
        None,
        ctypes.byref(extra),
        None,
        None,
        _UI_FORBIDDEN,
        ctypes.byref(out),
    ):
        raise DpapiError(f"{name} failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        # Unwrapped, it is the key: not left in freed memory. The copy returned is
        # Python's, which can't be wiped (ADR 0001).
        ctypes.memset(out.pbData, 0, out.cbData)
        kernel32.LocalFree(out.pbData)


def _blob(data: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob._buffer = buffer  # kept alive as long as the blob
    return blob


@cache
def _win32() -> tuple[Any, Any]:
    if sys.platform != "win32":
        raise DpapiError("DPAPI is a Windows service")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob = ctypes.POINTER(_Blob)
    for name, description in (
        ("CryptProtectData", wintypes.LPCWSTR),
        ("CryptUnprotectData", ctypes.POINTER(wintypes.LPWSTR)),
    ):
        function = getattr(crypt32, name)
        function.argtypes = [
            blob,
            description,
            blob,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob,
        ]
        function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32
