"""Reading an uploaded file, or a request's body, without taking more than a route
allows.

`await file.read()` loads the whole upload into memory before anything can check its
size; reading in chunks stops one chunk past the limit. For a file this bounds memory,
not disk: the multipart parser has already spooled the upload to a temporary file. A
JSON body declared as a model is read and parsed whole before the route runs, so a
route that bounds one reads it here instead.
"""

from __future__ import annotations

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
