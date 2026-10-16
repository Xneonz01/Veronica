"""A tool server for the tests: speaks the current or the older protocol over stdio, and
misbehaves on request. Standard library only; run as `python mcp_server.py <mode>`.

Modes: current, older, batches (2025-03-26, answers in batches), unsupported (-32022),
silent (never answers the probe), probe-kills (exits when probed, answers the older
way), crash (exits on a call), crash-soon (exits a moment after listing its tools), long
(sends one line of 5 MiB on a call), child (starts a sleeping child and prints its pid
on stderr), plus `changed` after any of them (a tool's description differs, as after
an update).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

MODES = set(sys.argv[1:]) or {"current"}
CURRENT = "2026-07-28"
META = "io.modelcontextprotocol/"
TOOLS = [
    {
        "name": "echo",
        "title": "Echo",
        "description": "Says the text back." + (" Now louder." if "changed" in MODES else ""),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "What to say."}},
            "required": ["text"],
        },
    },
    {
        "name": "fail",
        "description": "Always reports an error.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask",
        "description": "Asks for the user's input.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nested",
        "description": "Takes an object, which can't be offered.",
        "inputSchema": {"type": "object", "properties": {"o": {"type": "object"}}},
    },
]


def send(message: object) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def answer(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id: object, code: int, text: str, data: object = None) -> dict[str, object]:
    body: dict[str, object] = {"code": code, "message": text}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": body}


def older() -> bool:
    return bool(MODES & {"older", "batches", "silent", "probe-kills"})


def reply(message: dict[str, object]) -> object:
    method, request_id = message.get("method"), message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "server/discover":
        if "probe-kills" in MODES:
            sys.exit(4)
        if "silent" in MODES:
            return None
        if "unsupported" in MODES:
            return error(request_id, -32022, "Unsupported", {"supported": ["2099-01-01"]})
        if older():
            return error(request_id, -32601, "Method not found")
        result = {
            "resultType": "complete",
            "supportedVersions": [CURRENT],
            "capabilities": {"tools": {}},
            "_meta": {f"{META}serverInfo": {"name": "fake-current", "version": "1"}},
            "ttlMs": 300_000,
            "cacheScope": "public",
        }
        return answer(request_id, result)
    if method == "initialize":
        version = "2025-03-26" if "batches" in MODES else "2025-11-25"
        info = {"name": "fake-older", "version": "1"}
        return answer(
            request_id,
            {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": info},
        )
    if method == "tools/list":
        if not older() and f"{META}protocolVersion" not in params.get("_meta", {}):
            return error(request_id, -32602, "Missing _meta")
        return answer(request_id, {"tools": TOOLS, "ttlMs": 300_000, "cacheScope": "public"})
    if method == "tools/call":
        name, arguments = params.get("name"), params.get("arguments") or {}
        if "crash" in MODES:
            sys.exit(3)
        if "long" in MODES:
            sys.stdout.write("x" * (5 * 1024 * 1024) + "\n")
            sys.stdout.flush()
            return None
        if name == "fail":
            return answer(
                request_id, {"content": [{"type": "text", "text": "Broken."}], "isError": True}
            )
        if name == "ask":
            return answer(request_id, {"resultType": "input_required", "inputRequests": {"q": {}}})
        text = arguments.get("text", "")
        content = [
            {"type": "text", "text": f"You said: {text}"},
            {"type": "image", "data": "", "mimeType": "image/png"},
        ]
        return answer(request_id, {"content": content})
    if isinstance(method, str) and request_id is None:
        return None  # a notification
    return error(request_id, -32601, "Method not found")


def main() -> None:
    if "child" in MODES:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        sys.stderr.write(f"child {child.pid}\n")
        sys.stderr.flush()
    sys.stderr.write("fake server ready\n")
    sys.stderr.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        out = reply(message)
        if out is None:
            continue
        if "batches" in MODES and message.get("method") == "tools/list":
            send([out, {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}])
        else:
            send(out)
        if "crash-soon" in MODES and message.get("method") == "tools/list":
            time.sleep(0.2)
            sys.exit(5)
    time.sleep(0.05)


if __name__ == "__main__":
    main()
