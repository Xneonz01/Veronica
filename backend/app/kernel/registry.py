"""Capability declarations and the LLM tool schemas derived from them.

Each capability declares its arguments as a Pydantic model and the Ollama tool
schema is generated from that model, so validation and the schema can't drift.

Capabilities use dotted names (`files.read`) for policy, audit and REST. The model
is given the underscored form (`files_read`) because it calls those more reliably.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.kernel.capability import CapabilityResult, RiskTier

# Where a capability's effect lands. `local`: this computer. `network`: a request to a
# host on the internet, which carries its arguments out with it, so the egress gate
# reads them (`kernel/egress.py`) whatever its risk tier says about local harm.
Reach = Literal["local", "network"]
# A dotted id: a lowercase family, then one to three parts that may keep a tool's own
# case (`mcp.notes.getUser`). Anything else is refused at registration, before a bad
# name can reach a policy file, an audit row or a prompt.
_NAME = re.compile(r"[a-z][a-z0-9_]*(\.[A-Za-z0-9_-]+){1,3}")
_MAX_NAME = 200

# The argument is an instance of the capability's own `args_model`: the kernel validates
# with that model before calling. Typing the pairing would need a generic Capability,
# which buys nothing because the registry holds a mixed collection.
Handler = Callable[[Any], Awaitable[CapabilityResult]]
Preflight = Callable[[Any], Awaitable[CapabilityResult]]
Summarizer = Callable[[Any], str]

_DEFAULT_TIMEOUT_S = 10.0


def _default_summary(args: BaseModel) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in args.model_dump().items())[:120]


@dataclass(frozen=True, slots=True)
class Capability:
    """One declared capability: how to describe it, validate it, and run it."""

    name: str  # dotted id, e.g. "files.read"
    description: str  # one terse line — it costs prompt tokens on every turn
    args_model: type[BaseModel]
    risk: RiskTier
    handler: Handler
    timeout_s: float = _DEFAULT_TIMEOUT_S
    voice_exposed: bool = False  # offered to the model during voice calls
    summarize: Summarizer = field(default=_default_summary)
    # A guarded action that runs without asking unless the user's policy says
    # otherwise: one the notification it raises can undo. Never a dangerous one.
    auto_approve: bool = False
    # Arguments that only pick among the user's own memories ("the memory to forget"),
    # which the taint check reads against everything she read except her memory block:
    # copied from there is how they are meant to be written, and copied from a document
    # they would pick a memory for whoever wrote it.
    selectors: frozenset[str] = frozenset()
    # Run before anyone is asked, when the action needs confirmation. A refusal ends
    # the request without a prompt: "forget X" when nothing matches X would ask for a
    # yes that could only lead to "couldn't". Otherwise its `detail`, if any, is what
    # the prompt names in place of `summarize`: the memories a forget will remove,
    # not the model's words for them. It may keep what it named on the arguments
    # (`capabilities/memory_args.py`), so a yes changes exactly that rather than what a
    # second look finds later; the handler still checks that it is there.
    preflight: Preflight | None = None
    # Whether it is offered to the model right now, read every turn: a capability the
    # user switches on and off in Settings (the web) is registered once and offered
    # only while on. None: always. The kernel refuses a call made while it is false,
    # so a tool list built before the switch can't reach it.
    available: Callable[[], bool] | None = None
    reach: Reach = "local"
    # Arguments that are addresses: one the user didn't write and no search returned
    # asks before it is requested (`kernel/egress.py`).
    url_fields: frozenset[str] = frozenset()

    @property
    def tool_name(self) -> str:
        """The wire name the model calls (dots are unreliable in tool names)."""
        return self.name.replace(".", "_")


def to_tool_schema(capability: Capability) -> dict[str, Any]:
    """Derive the Ollama /api/chat tool schema from the capability's args model.

    Rejects nested models: Pydantic emits `$defs`/`$ref` for those, which the tool
    schema format does not carry. Failing here means a bad capability is caught at
    registration (startup) rather than as a confusing model error mid-conversation.
    """
    schema = capability.args_model.model_json_schema()
    if "$defs" in schema:
        raise ValueError(
            f"{capability.name}: args model must be flat (nested models emit $defs/$ref)"
        )
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)  # pure noise in the prompt
    return {
        "type": "function",
        "function": {
            "name": capability.tool_name,
            "description": capability.description,
            "parameters": schema,
        },
    }


class CapabilityRegistry:
    """Holds the declared capabilities and the tool schemas derived from them."""

    def __init__(self) -> None:
        self._by_name: dict[str, Capability] = {}
        self._by_tool_name: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        # Whole: `$` lets a final line break through, into a tool name the model reads.
        if len(capability.name) > _MAX_NAME or not _NAME.fullmatch(capability.name):
            raise ValueError(f"not a capability name: {capability.name[:_MAX_NAME]!r}")
        missing = capability.url_fields - set(capability.args_model.model_fields)
        if missing:
            raise ValueError(f"{capability.name}: no such argument for url_fields: {missing}")
        if capability.name in self._by_name:
            raise ValueError(f"capability already registered: {capability.name}")
        if capability.tool_name in self._by_tool_name:
            # `files.read` and `files_read` are distinct ids that collapse to the same
            # wire name. Silently overwriting would route the model's call to the wrong
            # handler while policy and audit still name the other one.
            raise ValueError(
                f"{capability.name}: tool name {capability.tool_name!r} is already taken by "
                f"{self._by_tool_name[capability.tool_name].name}"
            )
        to_tool_schema(capability)  # validate eagerly: a bad model fails at registration
        self._by_name[capability.name] = capability
        self._by_tool_name[capability.tool_name] = capability

    def get(self, name: str) -> Capability | None:
        """Look up by dotted id (policy, audit, REST)."""
        return self._by_name.get(name)

    def by_tool_name(self, tool_name: str) -> Capability | None:
        """Look up by the model-facing wire name (dispatch)."""
        return self._by_tool_name.get(tool_name)

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def schemas(self, capabilities: Iterable[Capability] | None = None) -> list[dict[str, Any]]:
        return [to_tool_schema(cap) for cap in (capabilities if capabilities is not None else self)]

    def offered(self, *, call: bool) -> list[Capability]:
        """What a turn offers the model: every capability that is available, or on a
        call only those marked `voice_exposed`. One rule for the product and the
        evaluations that measure it."""
        return [
            cap
            for cap in self
            if (cap.voice_exposed or not call) and (cap.available is None or cap.available())
        ]
