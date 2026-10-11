"""Row ids as they arrive from a URL.

SQLite stores a signed 64-bit integer, so a larger number raises `OverflowError`
inside the driver and would surface as a 500. Bounding the parameter turns that
into the typed 422 the frontend already renders, and rejects the value at the edge
instead of in the store.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import Field

_SQLITE_MAX_INT = 2**63 - 1

# A row id is always a positive integer that SQLite can actually hold.
RowId = Annotated[int, Path(ge=1, le=_SQLITE_MAX_INT)]

# The same bound for ids that arrive in a request body, where `Path()` does not apply.
BodyRowId = Annotated[int, Field(ge=1, le=_SQLITE_MAX_INT)]
