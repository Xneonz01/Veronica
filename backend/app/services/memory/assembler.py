"""The Context Assembler: builds the one memory block for the LLM prompt.

One component turns retrieved memory into a bounded context block: the Tier-1
profile (always, small) plus the relevance-gated memories for this turn. It stays
lean because it runs before the first token. Document (RAG) grounding for text chat
is assembled separately, in `chat/context.py`.
"""

from __future__ import annotations

from app.services.memory.address import ADDRESS_KEY, NAME_KEYS, address_directive
from app.services.memory.models import RetrievedContext
from app.services.memory.retriever import MemoryRetriever

_PREAMBLE = "What you remember about this user (use it naturally; do not recite it back):"


class ContextAssembler:
    """Turn a retrieval into the one bounded context block for the prompt."""

    def __init__(self, retriever: MemoryRetriever) -> None:
        self._retriever = retriever

    async def assemble(self, user_text: str) -> str | None:
        """Return the memory context block for this turn, or None if nothing fits."""
        context = await self._retriever.retrieve(user_text)
        return render_block(context)


RECALL_PREAMBLE = "What you remember about the user, relevant to what they just asked:"


def render_block(context: RetrievedContext, *, preamble: str = _PREAMBLE) -> str | None:
    lines: list[str] = []
    if context.profile:
        # Form-of-address is emitted as an explicit directive, not a recitable
        # "name: X" fact, so the model obeys it instead of echoing the name.
        directive = address_directive(context.profile)
        if directive:
            lines.append(directive)
        rest = {
            key: value
            for key, value in context.profile.items()
            if key not in NAME_KEYS and key != ADDRESS_KEY
        }
        if rest:
            facts = ", ".join(f"{key}: {value}" for key, value in rest.items())
            lines.append(f"Profile — {facts}.")
    if context.items:
        recalled = " ".join(item.memory.content.rstrip(".") + "." for item in context.items)
        lines.append(f"Recall — {recalled}")
    if not lines:
        return None
    return preamble + "\n" + "\n".join(lines)
