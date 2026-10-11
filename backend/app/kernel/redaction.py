"""What of a capability request is kept or shown: its arguments and its result's
detail, with secrets blanked and bulk clipped.

The audit log stores both, and a confirmation prompt shows the arguments, so both
take them from here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.memory.sensitive import is_secret_name, is_sensitive

_MAX_ARG_CHARS = 200


def redact_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """Strip secrets and bulk from arguments before they are stored or shown.

    `is_sensitive` is tuned for sentences ("my password is X"), so a bare value like
    {"password": "hunter2"} matches nothing on its own. A field whose name says it holds
    a secret is redacted whatever it holds, and every field is also checked as "<key>
    is <value>" and on its own.
    """
    out: dict[str, Any] = {}
    for key, value in args.items():
        text = str(value)
        # Clip before scanning: only the clipped text is ever stored, and scanning a
        # 256 KB file body twice would slow every audit write.
        clipped = text[:_MAX_ARG_CHARS]
        if is_secret_name(key) or is_sensitive(f"{key} is {clipped}") or is_sensitive(clipped):
            out[key] = "[redacted]"
        elif len(text) > _MAX_ARG_CHARS:
            out[key] = clipped + "…"  # a file body never lands in the log
        else:
            out[key] = value
    return out


def redact_detail(detail: str) -> str:
    """A result's detail can name the file it touched, so it is screened like an argument.

    A lone surrogate (a file name Windows allows, or JSON from the model) can't be
    stored as UTF-8, and the whole audit row was lost with it; it is kept as its escape.
    """
    clipped = _clip(detail).encode("utf-8", "backslashreplace").decode("utf-8")
    return "[redacted]" if is_sensitive(clipped) else clipped


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_ARG_CHARS else text[:_MAX_ARG_CHARS] + "…"
