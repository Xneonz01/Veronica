"""JSON-RPC 2.0 over a tool server's lines: requests and their answers, notifications,
and what the server asks of her.

One message per line (MCP's stdio framing): `encode` writes it, `Channel.received` reads
it on the event loop, where the reader thread hands it (`loop.call_soon_threadsafe`).
What arrives is a stranger's, so it is read defensively:

* **Unreadable lines are dropped**, and a line nested so deep that `json.loads` raises
  `RecursionError` is unreadable too; neither stops the reader.
* **Batches** (a JSON array) were part of only the 2025-03-26 revision: read then, at
  most 64 at once, and dropped otherwise.
* **An answer** is matched to a request still waiting, by id; a late one, after a
  timeout or a cancel, is dropped.
* **A request from the server**: the current revision forbids them on stdio and a
  client must never write a response, so one is dropped. Before it, `ping` is answered
  and anything else told it isn't supported (-32601): she offers a server nothing it
  could ask her for (no sampling, no roots).

At most `in_flight` requests wait at once; past that a request waits its turn.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

MAX_BATCH = 64
_NOT_SUPPORTED = -32601


class RpcError(Exception):
    """The server answered a request with an error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"{code}: {message[:200]}")
        self.code = code
        self.message = message
        self.data = data


class Gone(Exception):
    """The server stopped before it answered."""


def encode(message: dict[str, Any]) -> bytes:
    """One message as a line. `json.dumps` escapes every line break inside a string."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def decode(line: bytes) -> list[dict[str, Any]] | None:
    """The messages a line holds (a batch, or one), or None when it can't be read."""
    try:
        found = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if isinstance(found, dict):
        return [found]
    if isinstance(found, list):
        return [item for item in found if isinstance(item, dict)]
    return None


class Channel:
    """Requests to one server and its answers, used from the event loop."""

    def __init__(
        self,
        send: Callable[[bytes], Awaitable[None]],
        *,
        in_flight: int = 8,
        on_notification: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._send = send
        self._ids = itertools.count(1)
        self._waiting: dict[int, asyncio.Future[Any]] = {}
        self._slots = asyncio.Semaphore(in_flight)
        self._on_notification = on_notification
        self._gone: str | None = None
        self._sending: set[asyncio.Task[None]] = set()
        self._drops = 0  # kept, so none is collected early
        # Set once the era is agreed (`protocol.py`).
        self.batches = False
        self.answers_requests = True

    async def request(
        self, method: str, params: dict[str, Any], *, timeout: float, cancellable: bool = True
    ) -> Any:
        """The result of `method`; `RpcError` for an error, `TimeoutError`, or `Gone`. A
        request given up on is cancelled with the server, but never `initialize`."""
        async with self._slots:
            if self._gone is not None:
                raise Gone(self._gone)
            request_id = next(self._ids)
            answer: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._waiting[request_id] = answer
            message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            try:
                await self._send(encode(message))
                return await asyncio.wait_for(answer, timeout)
            except (TimeoutError, asyncio.CancelledError):
                if cancellable and self._gone is None:
                    self._cancel_later(request_id)
                raise
            except OSError as exc:
                raise Gone("its input closed") from exc
            finally:
                self._waiting.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self._send(encode(message))
        except OSError as exc:
            raise Gone("its input closed") from exc

    def received(self, line: bytes) -> None:
        """Read one line the server wrote. Never raises: it is the server's text."""
        self.take(decode(line), line.lstrip().startswith(b"["))

    def take(self, found: list[dict[str, Any]] | None, batch: bool) -> None:
        """Act on a line already decoded (`decode`, on the reader's thread, so a run of
        long lines doesn't hold up the event loop). `batch`: it was a JSON array."""
        if found is None:
            self._dropped("a line that isn't JSON")
            return
        if (batch or len(found) > 1) and (not self.batches or len(found) > MAX_BATCH):
            self._dropped("a batch it may not")
            return
        for message in found:
            self._one(message)

    def _dropped(self, what: str) -> None:
        """Log what was dropped, the first time and then every hundredth: a server can
        send these as fast as it likes."""
        self._drops += 1
        if self._drops % 100 == 1:
            logger.info("a tool server sent %s; dropped (%d so far)", what, self._drops)

    def gone(self, why: str) -> None:
        """The server stopped: every request still waiting fails with `Gone`."""
        self._gone = why
        for answer in self._waiting.values():
            if not answer.done():
                answer.set_exception(Gone(why))
        self._waiting.clear()

    def _one(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if "id" in message and ("result" in message or "error" in message):
            answer = self._waiting.get(message["id"]) if _plain_id(message["id"]) else None
            if answer is None or answer.done():
                return  # late, after a timeout or a cancel, or never asked
            error = message.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                answer.set_exception(
                    RpcError(
                        code if isinstance(code, int) else 0,
                        str(error.get("message", "")),
                        error.get("data"),
                    )
                )
            else:
                answer.set_result(message.get("result"))
        elif isinstance(method, str) and "id" in message:
            self._asked(method, message["id"])
        elif isinstance(method, str) and self._on_notification is not None:
            params = message.get("params")
            self._on_notification(method, params if isinstance(params, dict) else {})

    def _asked(self, method: str, request_id: Any) -> None:
        if not self.answers_requests or not _plain_id(request_id):
            self._dropped("a request it may not")
            return
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if method == "ping":
            reply["result"] = {}
        else:
            reply["error"] = {"code": _NOT_SUPPORTED, "message": "Not supported"}
        self._later(self._send(encode(reply)))

    def _cancel_later(self, request_id: int) -> None:
        cancel = {"requestId": request_id, "reason": "No longer needed"}
        self._later(self.notify("notifications/cancelled", cancel))

    def _later(self, sending: Awaitable[None]) -> None:
        async def send() -> None:
            try:
                await sending
            except (OSError, Gone):
                pass  # it has gone; nothing to tell it

        task = asyncio.get_running_loop().create_task(send())
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)


def _plain_id(value: Any) -> bool:
    """A JSON-RPC id as the spec allows: a string or an integer, never a list or dict."""
    return isinstance(value, str | int) and not isinstance(value, bool)
