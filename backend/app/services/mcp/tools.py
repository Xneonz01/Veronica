"""A tool server's tools as her capabilities: approved by the user, named, asked about
every time, and their results made into text she reads as data.

* **Approved, and pinned.** A tool is offered only once the user has approved it in
  Settings, and only as it was when approved: its pin is the SHA-256 of its name, title,
  description, input schema and annotations, in canonical JSON (`pin`). A server that
  changes a tool's description after approval, to steer her, has it withheld again
  until the user looks at it anew.
* **Named** `mcp.<server>.<tool>`, the tool's name kept but for a dot, which the
  registry reads as a separator; two names that come out the same are a collision and
  the second is left out.
* **Asked about every time** (`always_ask`, `guarded`), never on a call, and refused
  when the server isn't running (`available`).
* **Results**: the text items joined, any other kind named in words, cleaned of what
  renders as nothing and cut at 8,000 characters (`as_text`). An error the tool reports
  (`isError`) is a failure, said in her words: the tool's own aren't passed on, since a
  failure is told to the model unfenced (`tools/render.py`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.adapters.llm.tokens import estimate
from app.core.text_safety import clean_untrusted
from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.errors import Code
from app.kernel.registry import Capability
from app.services.mcp.schema import Unsupported, arguments_model, clean, shown

MAX_RESULT = 8_000
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_PINNED = ("name", "title", "description", "inputSchema", "annotations")

Call = Callable[[str, dict[str, Any]], Awaitable[CapabilityResult]]


def pin(tool: dict[str, Any]) -> str:
    """The fingerprint a tool is approved under: what she is told of it, and what it
    takes, exactly."""
    pinned = {key: tool.get(key) for key in _PINNED}
    canonical = json.dumps(pinned, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def capability_name(server: str, tool: str) -> str:
    return f"mcp.{server}.{tool.replace('.', '_')}"


@dataclass(frozen=True, slots=True)
class Tool:
    """One tool as a server listed it, read: offered, or why not."""

    name: str  # as the server calls it
    capability: str  # as the kernel does
    pin: str
    title: str  # shown, revealed
    description: str  # shown, revealed
    told: str  # what the model is told, cleaned
    args: type[BaseModel] | None  # None: unsupported
    unsupported: str = ""

    @property
    def cost(self) -> int:
        """What it takes of the chat window, offered: its name, what she is told of it,
        and its arguments' schema, as the tool list carries them."""
        parameters = self.args.model_json_schema() if self.args is not None else {}
        listed = {"name": self.capability, "description": self.told, "parameters": parameters}
        return estimate(json.dumps(listed))

    def view(self, approved: str | None) -> dict[str, Any]:
        """What Settings shows of it: approved, new, or changed since it was."""
        state = "approved" if approved == self.pin else ("changed" if approved else "new")
        if self.args is None:
            state = "unsupported"
        return {
            "name": self.name,
            "capability": self.capability,
            "title": self.title,
            "description": self.description,
            "state": state,
            "unsupported": self.unsupported,
            "arguments": sorted(self.args.model_fields) if self.args is not None else [],
            # Sent back with an approval: what the user read is what is approved.
            "pin": self.pin,
        }


def read_tools(server: str, listed: list[dict[str, Any]]) -> list[Tool]:
    """The server's list as tools, first come first kept when two names collide."""
    tools: list[Tool] = []
    taken: set[str] = set()
    for raw in listed:
        name = raw.get("name")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            continue
        capability = capability_name(server, name)
        if capability in taken or capability.replace(".", "_") in taken:
            continue
        taken |= {capability, capability.replace(".", "_")}
        title = _text(raw.get("title")) or _text(_field(raw.get("annotations"), "title")) or name
        try:
            args: type[BaseModel] | None = arguments_model(name, raw.get("inputSchema"))
            why = ""
        except Unsupported as refused:
            args, why = None, str(refused)
        tools.append(
            Tool(
                name=name,
                capability=capability,
                pin=pin(raw),
                title=shown(title, 80),
                description=shown(raw.get("description")),
                told=clean(raw.get("description")),
                args=args,
                unsupported=why,
            )
        )
    return tools


def capability(
    tool: Tool,
    server_name: str,
    call: Call,
    *,
    running: Callable[[], bool],
    timeout_s: float,
    internet: bool = False,
) -> Capability:
    """`tool` as a capability. Only for a supported tool the user approved. `internet`:
    the user said its server reaches the internet, and its prompt says so."""
    assert tool.args is not None
    told = tool.told or tool.title
    description = f"{told} (a tool from {clean(server_name, 60)})"

    async def handler(args: BaseModel) -> CapabilityResult:
        return await call(tool.name, args.model_dump(exclude_unset=True))

    def summary(args: BaseModel) -> str:
        sent = ", ".join(f"{k}={v!r}" for k, v in args.model_dump(exclude_unset=True).items())
        where = f"{tool.title} from {clean(server_name, 60)}"
        if internet:
            where += ", which reaches the internet"
        return where + (f": {sent}" if sent else "")

    return Capability(
        name=tool.capability,
        description=description,
        args_model=tool.args,
        risk=RiskTier.guarded,
        handler=handler,
        timeout_s=timeout_s,
        summarize=summary,
        available=running,
        reach="external",
        always_ask=True,
    )


def result(found: dict[str, Any], server_name: str) -> CapabilityResult:
    """A `tools/call` result as hers to read: content, or the tool's own error."""
    text = as_text(found)
    if found.get("isError") is True:
        detail = f"{clean(server_name, 60)} said that didn't work."
        return CapabilityResult(ok=False, code=Code.tool_error, detail=detail)
    shown_line = f"Used a tool from {clean(server_name, 60)}."
    return CapabilityResult(ok=True, detail=shown_line, data={"content": text or "(no result)"})


def as_text(found: dict[str, Any]) -> str:
    """Every item of a result in words: text as it is, anything else named."""
    parts: list[str] = []
    content = found.get("content")
    for item in content if isinstance(content, list) else []:
        if isinstance(item, dict):
            parts.append(_item(item))
    if not any(parts) and "structuredContent" in found:
        try:
            parts.append(json.dumps(found["structuredContent"], ensure_ascii=False)[:MAX_RESULT])
        except (TypeError, ValueError):
            parts.append("(a result that can't be read)")
    text = clean_untrusted("\n".join(part for part in parts if part)).strip()
    return text if len(text) <= MAX_RESULT else text[:MAX_RESULT] + "\n… (cut short)"


def _field(value: Any, key: str) -> Any:
    """`value[key]` when `value` is an object: a server's list is a stranger's, and one
    tool listed oddly mustn't stop the others."""
    return value.get(key) if isinstance(value, dict) else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _item(item: dict[str, Any]) -> str:
    kind = item.get("type")
    if kind == "text":
        return str(item.get("text", ""))[: MAX_RESULT + 1]
    if kind == "resource":
        resource = item.get("resource")
        if isinstance(resource, dict) and isinstance(resource.get("text"), str):
            return str(resource["text"])[: MAX_RESULT + 1]
        return "(a file it sent, not shown)"
    if kind == "resource_link":
        return f"(a link to {clean(item.get('name') or item.get('uri'), 120)})"
    if kind == "image":
        return "(an image it sent, not shown)"
    if kind == "audio":
        return "(a sound clip it sent, not played)"
    return "(something it sent that I can't read)"
