"""Scrub secrets out of anything the telemetry writers put on disk.

Callers hand raw values to the black box and the writer scrubs them, so a call site
that forgets to redact cannot leak a token into a file a user might attach to a
bug report. The specific shapes run before the generic high-entropy sweeps, so a
redacted JWT is not matched again as "a long base64 run".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.paths import INSTALL_ROOT, INSTALL_ROOT_AS_LOADED

PLACEHOLDER = "[REDACTED]"
_ELLIPSIS = "…"

# Tracebacks and log messages carry absolute paths, which give away the account name
# and where the project lives. The install folder becomes `<veronica>` and the home
# folder `~`, in that order because the first usually sits inside the second; any
# other account's folder becomes `<user>`.
_ROOTS = {str(INSTALL_ROOT_AS_LOADED), str(INSTALL_ROOT)}  # differ behind a junction
_KNOWN_FOLDERS = [
    (variant, label)
    for folders, label in ((_ROOTS, "<veronica>"), ({str(Path.home())}, "~"))
    for variant in sorted(
        {f for folder in folders for f in (folder, folder.replace("\\", "/"))},
        key=len,
        reverse=True,
    )
]
_USER_DIR = re.compile(r"(?i)(\b[A-Z]:[\\/]+Users[\\/]+|/home/|/Users/)[^\\/\s\"':]+")

# The credential after an auth header name: `Authorization: Bearer eyJ...`. The
# optional scheme word matters, otherwise only "Bearer" would be replaced.
_AUTH_HEADER = re.compile(
    r"(?i)\b(authorization)\s*[:=]\s*(?:(?:bearer|basic|token|digest|apikey)\s+)?\S+"
)
# A bare `Bearer <token>`, which is how tokens show up inside HTTP library errors.
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")
# Provider key shapes: sk-..., GitHub ghp_/gho_/ghu_/ghs_/ghr_, Groq gsk_, Slack xox?-.
_PROVIDER_KEY = re.compile(r"\b(?:sk-|gh[pousr]_|gsk_|xox[baprs]-)[A-Za-z0-9_\-]{12,}")
_AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
)
# `password=hunter2`, `api_key: "abc"`, `client_secret=xyz`: keep the key name so the
# line stays readable, replace the value. The optional prefix catches compound names
# (`client_secret`), and the lookahead keeps `token_count=5` untouched.
_SECRET_KV = re.compile(
    r"(?i)\b([A-Za-z0-9]*[_-])?"
    r"(password|passcode|passphrase|api[_-]?key|secret|token)(?![A-Za-z0-9_])"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
# Credentials embedded in a URL: scheme://user:pass@host.
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@")
# 32+ hex characters: hashes, raw keys, device tokens.
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
# 32+ base64 characters mixing upper, lower and digits. Requiring all three keeps
# long words and file paths (no digits) out of it.
_LONG_B64 = re.compile(
    r"\b(?=[A-Za-z0-9+/]*[a-z])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[0-9])"
    r"[A-Za-z0-9+/]{32,}={0,2}"
)
# The urlsafe alphabet `secrets.token_urlsafe` produces; `-` and `_` break the run
# above into short pieces, so it needs its own rule.
_LONG_B64URL = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[0-9])"
    r"[A-Za-z0-9_-]{32,}={0,2}(?![A-Za-z0-9_-])"
)
# A structured field whose name says it holds a secret. The text rules can't see
# `record(..., password="x")` because there is no `password=` inside any string.
# Anchored at the end of the name so `token_count` and `prompt_tokens` survive.
_SECRET_FIELD_NAME = re.compile(
    r"(?i)(?:^|[._-])"
    r"(?:password|passcode|passphrase|api[_-]?key|secret|token|credential|authorization"
    r"|key|auth|bearer|cookie)$"
)


def scrub_paths(text: str) -> str:
    """Hide the install folder, the home folder and other accounts' names in paths."""
    for folder, label in _KNOWN_FOLDERS:
        if len(folder) > 3:  # never collapse a bare drive root
            text = re.sub(re.escape(folder), label, text, flags=re.IGNORECASE)
    return _USER_DIR.sub(r"\1<user>", text)


def redact(text: str, clip: int) -> str:
    """Replace known secret shapes and account names in `text`, then clip it."""
    out = scrub_paths(text)
    out = _PRIVATE_KEY.sub(PLACEHOLDER, out)
    out = _AUTH_HEADER.sub(rf"\1: {PLACEHOLDER}", out)
    out = _BEARER.sub(f"Bearer {PLACEHOLDER}", out)
    out = _URL_CREDENTIALS.sub(rf"\1{PLACEHOLDER}@", out)
    out = _PROVIDER_KEY.sub(PLACEHOLDER, out)
    out = _AWS_KEY.sub(PLACEHOLDER, out)
    out = _JWT.sub(PLACEHOLDER, out)
    out = _SECRET_KV.sub(rf"\1\2\3{PLACEHOLDER}", out)
    out = _LONG_HEX.sub(PLACEHOLDER, out)
    out = _LONG_B64.sub(PLACEHOLDER, out)
    out = _LONG_B64URL.sub(PLACEHOLDER, out)
    return clip_text(out, clip)


def clip_text(text: str, clip: int) -> str:
    """Limit a string to `clip` characters, keeping its start and its end.

    The cut goes in the middle, marked with an ellipsis, because the end matters as
    much as the start: it is where a traceback names the failing line and the error.
    """
    if clip <= 0:
        return ""
    if len(text) <= clip:
        return text
    head = (clip - 1) // 2
    tail = clip - 1 - head
    return text[:head] + _ELLIPSIS + text[len(text) - tail :]


def redact_value(value: Any, clip: int) -> Any:
    """Redact strings inside dicts, lists and tuples, keeping JSON types intact.

    Numbers, booleans and None pass through unchanged. Any other object is turned
    into its repr first, because an arbitrary object would make the JSON encoder
    raise inside the writer thread.
    """
    if isinstance(value, str):
        return redact(value, clip)
    if isinstance(value, dict):
        return {
            str(key): (
                PLACEHOLDER if _SECRET_FIELD_NAME.search(str(key)) else redact_value(item, clip)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item, clip) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(repr(value), clip)
