"""Reading an uploaded file, or a request's body, without taking more than a route
allows.

`await file.read()` loads the whole upload into memory before anything can check its
size; reading in chunks stops one chunk past the limit. For a file this bounds memory,
not disk: the multipart parser has already spooled the upload to a temporary file. A
JSON body declared as a model is read and parsed whole before the route runs, so a
route that bounds one reads it here instead.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import Request, UploadFile

from app.api.refusals import Refusal

# Big enough for a typical PDF, phone photo or voice recording, small enough that one
# upload can't put a gigabyte in the data folder.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
TOO_LARGE = "That file is larger than I can take (25 MB)."
_READ_CHUNK_BYTES = 256 * 1024


async def read_capped(file: UploadFile, limit: int) -> bytes | None:
    """Read up to `limit` bytes, then stop. None if the file is bigger."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def read_body_capped(request: Request, limit: int) -> bytes | None:
    """The request's body, up to `limit` bytes. None if it declares or sends more."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def read_or_refuse(file: UploadFile) -> bytes:
    """`read_capped`, answering a file over the limit with a typed 422 and plain words."""
    data = await read_capped(file, MAX_UPLOAD_BYTES)
    if data is None:
        raise Refusal(422, "file_too_large", TOO_LARGE)
    return data


# Where an upload's copy lives while a parser reads it: inside her data folder, kept
# private and swept at start-up, not the temp folder every program on the account
# shares, where a crash left the copy for good.
UPLOADS_DIR = "uploads"
_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


@contextmanager
def private_copy(request: Request, data: bytes, name: str) -> Iterator[Path]:
    """A file holding `data`, named with `name`'s suffix (the parser goes by it), that
    exists only inside the `with` block."""
    folder = request.app.state.settings.data_path() / UPLOADS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix.lower()
    with tempfile.NamedTemporaryFile(
        dir=folder, delete=False, suffix=suffix if _SUFFIX.match(suffix) else ".bin"
    ) as handle:
        handle.write(data)
    path = Path(handle.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def sweep_uploads(data_dir: Path) -> None:
    """Remove copies a crash left behind: they are copies of what was indexed, never the
    only one of anything."""
    folder = data_dir / UPLOADS_DIR
    if folder.is_dir():
        for leftover in folder.iterdir():
            if leftover.is_file():
                leftover.unlink(missing_ok=True)
