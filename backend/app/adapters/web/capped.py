"""One GET, read no further than a limit, compressed or not.

A page or a search answer is fed from outside, so its size is the sender's choice. The
limit holds for what arrives and for what it decompresses to: `httpx` decompresses a
whole network chunk before anything sees it, and brotli at over a million to one, so a
211-byte reply reached half a gigabyte before a cap counted it. So the body is read raw,
only `gzip` and `deflate` are asked for and accepted (no `br`, no `zstd`, never two
stacked), and it is decompressed here in steps that can't produce more than the room
left. Redirects are never followed here; the caller decides what to do with one.
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

ACCEPT_ENCODING = "gzip, deflate"
_GZIP = 16 + zlib.MAX_WBITS
_ZLIB = zlib.MAX_WBITS
_RAW_DEFLATE = -zlib.MAX_WBITS


class TooLarge(Exception):
    """The body went past the limit, compressed or decompressed."""


class BadEncoding(Exception):
    """The body is compressed in a way that isn't read here, or is corrupt."""


@dataclass(frozen=True, slots=True)
class Fetched:
    status: int
    headers: httpx.Headers
    body: bytes


async def get_capped(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    *,
    limit: int,
    params: Mapping[str, str | int] | None = None,
    headers: Mapping[str, str] | None = None,
    extensions: dict[str, object] | None = None,
) -> Fetched:
    """The response to a GET of `url`, its body cut off with `TooLarge` past `limit`
    bytes raw or decoded, `BadEncoding` for a body that can't be read, and
    `httpx.HTTPError` for a connection that failed or timed out."""
    sent = {**(headers or {}), "Accept-Encoding": ACCEPT_ENCODING}
    request = client.build_request("GET", url, params=params, headers=sent, extensions=extensions)
    response = await client.send(request, stream=True, follow_redirects=False)
    try:
        if response.is_stream_consumed:
            # A response built in memory (`httpx.Response(content=...)`, a test's) read
            # its body when it was made; one from the network never has.
            if len(response.content) > limit:
                raise TooLarge
            return Fetched(response.status_code, response.headers, response.content)
        decoder = Decoder(response.headers.get("content-encoding", ""), limit)
        received = 0
        async for chunk in response.aiter_raw():
            received += len(chunk)
            if received > limit:
                raise TooLarge
            decoder.feed(chunk)
        return Fetched(response.status_code, response.headers, decoder.finish())
    finally:
        await response.aclose()


class Decoder:
    """A body as it arrives, decompressed into at most `limit` bytes."""

    def __init__(self, encoding: str, limit: int) -> None:
        name = encoding.strip().lower()
        if "," in name:
            raise BadEncoding  # stacked: each layer multiplies what one chunk can become
        if name in ("", "identity"):
            self._z = None
        elif name in ("gzip", "x-gzip"):
            self._z = zlib.decompressobj(_GZIP)
        elif name == "deflate":
            self._z = zlib.decompressobj(_ZLIB)
        else:
            raise BadEncoding
        self._deflate = name == "deflate"
        self._started = False
        self._limit = limit
        self._out = bytearray()

    def feed(self, chunk: bytes) -> None:
        if self._z is None:
            self._keep(chunk)
            return
        try:
            self._inflate(chunk)
        except zlib.error:
            # "deflate" is meant to be zlib-wrapped, and some servers send it raw: like
            # httpx, try raw once, from the first chunk only.
            if not (self._deflate and not self._started):
                raise BadEncoding from None
            self._z, self._deflate = zlib.decompressobj(_RAW_DEFLATE), False
            try:
                self._inflate(chunk)
            except zlib.error:
                raise BadEncoding from None
        self._started = True

    def finish(self) -> bytes:
        """The whole body. Nothing is flushed while input waits: after the steps of
        `feed` there is none, so the decompressor holds at most its own small window."""
        if self._z is not None and not self._z.eof:
            try:
                self._keep(self._z.flush())
            except zlib.error:
                raise BadEncoding from None
        return bytes(self._out)

    def _inflate(self, data: bytes) -> None:
        assert self._z is not None
        while data and not self._z.eof:
            room = self._limit - len(self._out) + 1  # one byte over proves too much
            self._keep(self._z.decompress(data, room))
            data = self._z.unconsumed_tail

    def _keep(self, piece: bytes) -> None:
        self._out += piece
        if len(self._out) > self._limit:
            raise TooLarge
