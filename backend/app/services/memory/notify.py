"""The notification payload for a memory write, and for what learning filed.

Presentation for the toast and awareness surfaces, kept out of `service.py` and
`learning.py` because it isn't memory logic. Both call it on the way out to the bus.
"""

from __future__ import annotations

from typing import Any

from app.services.memory.address import ADDRESS_KEY, address_summary, coerce_address
from app.services.memory.models import Memory, MemoryKind

# Bus op -> the user-facing notification action.
_ACTIONS = {
    "add": "added",
    "approve": "added",
    "candidate": "candidate",
    "update": "updated",
    "profile": "updated",
    "supersede": "superseded",
    "delete": "removed",
}


def summarize_memory(memory: Memory) -> str:
    """A short human one-liner for a notification (content is already third-person)."""
    if memory.kind is MemoryKind.profile and memory.key == ADDRESS_KEY:
        return address_summary(coerce_address(memory.content))
    if memory.kind is MemoryKind.profile and memory.key:
        return f"{memory.key} → {memory.content}".strip()
    text = " ".join(memory.content.split()).rstrip(".")
    return text if len(text) <= 80 else text[:79].rstrip() + "…"


def notify_payload(op: str, memory: Memory, *, origin: str) -> dict[str, Any]:
    """The payload for a memory.updated event, with what a notification needs.

    `origin` is "agent" (a write Veronica made), "ui" (the user's own Memory-page
    edit) or "undo"; only agent writes can raise a notification. `op`/`id` stay for
    back-compat. `undoable` covers the reversible-without-history cases.
    """
    return {
        "op": op,
        "id": memory.id,
        "memory_id": memory.id,
        "kind": memory.kind.value,
        "action": _ACTIONS.get(op, op),
        "summary": summarize_memory(memory),
        "confidence": memory.confidence,
        "source": memory.source,  # frontend: explicit = (source == "user")
        "undoable": op in {"add", "supersede", "candidate"} and origin == "agent",
        "origin": origin,
        "ts": memory.updated_at.isoformat(),
    }


def learned_payload(memory: Memory, *, active: bool, coalesce_id: str) -> dict[str, Any]:
    """The payload for a memory.learned event: every learned item surfaces, a notice
    with Undo when it was filed active, else a candidate for review. Items from one
    extraction share `coalesce_id`, so the app collapses them into one notice."""
    return {
        "id": memory.id,
        "memory_id": memory.id,
        "content": memory.content,  # back-compat
        "summary": summarize_memory(memory),
        "kind": memory.kind.value,
        "confidence": memory.confidence,
        "active": active,
        "inferred": True,
        "source": "inferred",
        "action": "added" if active else "candidate",
        "undoable": True,
        "origin": "agent",
        "coalesce_id": coalesce_id,
        "ts": memory.updated_at.isoformat(),
    }
