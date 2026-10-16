"""JSON-RPC with a tool server, in memory: what is written, what is read and dropped,
and the protocol's rules for each era (`services/mcp/rpc.py`, `protocol.py`)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from app.services.mcp import protocol
from app.services.mcp.rpc import Channel, Gone, RpcError, decode, encode


class Wire:
    """A server at the other end of a channel: answers each request with `answer`."""

    def __init__(self, answer: Callable[[dict[str, Any]], Any] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answer = answer
        self.channel = Channel(self.send, in_flight=2)

    async def send(self, line: bytes) -> None:
        assert line.endswith(b"\n") and b"\n" not in line[:-1]
        message = json.loads(line)
        self.sent.append(message)
        if self.answer is not None and "id" in message and "method" in message:
            reply = self.answer(message)
            if reply is not None:
                asyncio.get_running_loop().call_soon(
                    self.channel.received, json.dumps(reply).encode()
                )

    def methods(self) -> list[str]:
        return [m["method"] for m in self.sent if "method" in m]


def result(message: dict[str, Any], value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message["id"], "result": value}


def test_a_message_is_one_line_whatever_it_holds() -> None:
    line = encode({"text": "two\nlines\r\nand \u2028 more"})
    assert line.count(b"\n") == 1 and line.endswith(b"\n")
    assert decode(line) == [{"text": "two\nlines\r\nand \u2028 more"}]


@pytest.mark.parametrize(
    "line",
    [b"not json", b"[" * 100_000 + b"]" * 100_000, b"42", b'"x"'],
    ids=["not-json", "nested-too-deep", "a-number", "a-string"],
)
def test_a_line_that_cant_be_read_is_dropped(line: bytes) -> None:
    assert decode(line) is None


async def test_an_answer_comes_back_to_its_request() -> None:
    wire = Wire(lambda m: result(m, {"echo": m["params"]["x"]}))
    assert await wire.channel.request("a", {"x": 1}, timeout=1) == {"echo": 1}


async def test_an_error_is_raised_with_its_code_and_data() -> None:
    wire = Wire(
        lambda m: {
            "jsonrpc": "2.0",
            "id": m["id"],
            "error": {"code": -32022, "message": "no", "data": [1]},
        }
    )
    with pytest.raises(RpcError) as raised:
        await wire.channel.request("a", {}, timeout=1)
    assert (raised.value.code, raised.value.data) == (-32022, [1])


async def test_a_request_given_up_on_is_cancelled_and_its_late_answer_dropped() -> None:
    wire = Wire()
    with pytest.raises(TimeoutError):
        await wire.channel.request("slow", {}, timeout=0.05)
    await asyncio.sleep(0)
    cancelled = [m for m in wire.sent if m.get("method") == "notifications/cancelled"]
    assert cancelled and cancelled[0]["params"]["requestId"] == wire.sent[0]["id"]
    wire.channel.received(json.dumps(result(wire.sent[0], "late")).encode())  # no error


async def test_initialize_is_never_cancelled() -> None:
    wire = Wire()
    with pytest.raises(TimeoutError):
        await wire.channel.request("initialize", {}, timeout=0.05, cancellable=False)
    await asyncio.sleep(0)
    assert wire.methods() == ["initialize"]


async def test_no_more_than_in_flight_requests_wait_at_once() -> None:
    wire = Wire()  # never answers
    waiting = [asyncio.create_task(wire.channel.request("a", {}, timeout=5)) for _ in range(3)]
    await asyncio.sleep(0.05)
    assert len([m for m in wire.sent if m.get("method") == "a"]) == 2
    wire.channel.gone("stopped")
    for task in waiting:
        with pytest.raises(Gone):
            await task


async def test_a_server_that_goes_fails_every_waiting_request() -> None:
    wire = Wire()
    task = asyncio.create_task(wire.channel.request("a", {}, timeout=5))
    await asyncio.sleep(0)
    wire.channel.gone("exited")
    with pytest.raises(Gone):
        await task
    with pytest.raises(Gone):
        await wire.channel.request("b", {}, timeout=1)


async def test_an_older_server_is_answered_ping_and_told_nothing_else_is_supported() -> None:
    wire = Wire()
    wire.channel.received(b'{"jsonrpc":"2.0","id":7,"method":"ping"}')
    wire.channel.received(b'{"jsonrpc":"2.0","id":8,"method":"sampling/createMessage"}')
    await asyncio.sleep(0)
    assert wire.sent[0] == {"jsonrpc": "2.0", "id": 7, "result": {}}
    assert wire.sent[1]["error"]["code"] == -32601


async def test_a_current_server_is_never_answered() -> None:
    wire = Wire()
    wire.channel.answers_requests = False
    wire.channel.received(b'{"jsonrpc":"2.0","id":7,"method":"ping"}')
    await asyncio.sleep(0)
    assert wire.sent == []


async def test_a_batch_is_read_only_where_its_revision_allowed_it() -> None:
    seen: list[str] = []
    channel = Channel(_nowhere, on_notification=lambda method, _p: seen.append(method))
    batch = json.dumps([{"jsonrpc": "2.0", "method": "a"}, {"jsonrpc": "2.0", "method": "b"}])
    channel.received(batch.encode())
    assert seen == []
    channel.batches = True
    channel.received(batch.encode())
    assert seen == ["a", "b"]
    channel.received(json.dumps([{"jsonrpc": "2.0", "method": "c"}] * 65).encode())
    assert seen == ["a", "b"]  # past 64 at once: dropped


async def _nowhere(line: bytes) -> None:
    return None


def _discover(m: dict[str, Any]) -> Any:
    if m["method"] == "server/discover":
        meta = m["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        assert meta["io.modelcontextprotocol/clientCapabilities"] == {}
        found = {
            "supportedVersions": ["2026-07-28"],
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "S"}},
        }
        return result(m, found)
    if m["method"] == "tools/list":
        assert "io.modelcontextprotocol/protocolVersion" in m["params"]["_meta"]
        page = m["params"].get("cursor")
        tools = [{"name": f"t{page or 0}"}]
        return result(m, {"tools": tools, "nextCursor": None if page else "1", "ttlMs": 60_000})
    return None


async def test_the_current_era_needs_no_handshake_and_carries_meta_on_every_request() -> None:
    wire = Wire(_discover)
    era = await protocol.agree(wire.channel)
    assert era == protocol.Era("2026-07-28", current=True, name="S")
    tools, ttl = await protocol.list_tools(wire.channel, era)
    assert [t["name"] for t in tools] == ["t0", "t1"] and ttl == 60_000
    assert "initialize" not in wire.methods()


async def test_any_other_error_to_the_probe_means_the_older_era() -> None:
    def older(m: dict[str, Any]) -> Any:
        if m["method"] == "server/discover":
            return {"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32602, "message": "?"}}
        if m["method"] == "initialize":
            assert m["params"]["protocolVersion"] == "2025-11-25"
            return result(m, {"protocolVersion": "2025-06-18", "serverInfo": {"name": "Old"}})
        return None

    wire = Wire(older)
    era = await protocol.agree(wire.channel)
    assert era == protocol.Era("2025-06-18", current=False, name="Old")
    await asyncio.sleep(0)
    assert wire.methods() == ["server/discover", "initialize", "notifications/initialized"]
    assert not wire.channel.batches


async def test_an_older_version_she_doesnt_know_is_refused() -> None:
    def odd(m: dict[str, Any]) -> Any:
        if m["method"] == "initialize":
            return result(m, {"protocolVersion": "2023-01-01"})
        return {"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "?"}}

    with pytest.raises(protocol.Disagreed, match="2023-01-01"):
        await protocol.agree(Wire(odd).channel)


async def test_a_call_that_only_sends_state_back_is_tried_again_with_it() -> None:
    calls: list[dict[str, Any]] = []

    def stateful(m: dict[str, Any]) -> Any:
        calls.append(m["params"])
        if len(calls) == 1:
            return result(m, {"resultType": "input_required", "requestState": "abc"})
        return result(m, {"content": [{"type": "text", "text": "done"}]})

    era = protocol.Era("2026-07-28", current=True)
    found = await protocol.call_tool(Wire(stateful).channel, era, "t", {}, timeout=1)
    assert found["content"][0]["text"] == "done"
    assert calls[1]["requestState"] == "abc"


async def test_a_result_of_a_kind_she_doesnt_know_is_invalid() -> None:
    era = protocol.Era("2026-07-28", current=True)
    wire = Wire(lambda m: result(m, {"resultType": "something_new"}))
    with pytest.raises(protocol.Invalid):
        await protocol.call_tool(wire.channel, era, "t", {}, timeout=1)
