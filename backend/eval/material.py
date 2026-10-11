"""What a turn reads, built as the product builds it: her memory block as the assembler
renders it, and documents fenced as a chat turn fences them (`chat/context.py`). Shared
by the evaluations, so a prompt in one is the prompt in all.
"""

from __future__ import annotations

import secrets

from app.services.chat.context import document_fence, documents_header
from app.services.memory.assembler import render_block
from app.services.memory.models import (
    Memory,
    MemoryKind,
    RetrievedContext,
    RetrievedMemory,
)


def memory_block(recalled: list[str], profile: dict[str, str] | None = None) -> str:
    """These memories as the turn's memory block, as if retrieval had found them all
    relevant (it leaves out one that isn't, unless pinned); empty when there is nothing
    to show."""
    items = [
        RetrievedMemory(
            memory=Memory(
                id=index,
                kind=MemoryKind.fact,
                content=content,
                normalized_content=content.lower(),
                importance=0.5,
                confidence=1.0,
                pinned=False,
                source="user",
            ),
            score=1.0,
            semantic=1.0,
            lexical=1.0,
        )
        for index, content in enumerate(recalled, start=1)
    ]
    return render_block(RetrievedContext(profile=profile or {}, items=items)) or ""


def documents_block(documents: list[tuple[str, str]]) -> str:
    """(title, text) pairs as the documents a turn cites, under a marker only this
    turn knows."""
    nonce = secrets.token_hex(4)
    fences = [
        document_fence(index, title, text, nonce)
        for index, (title, text) in enumerate(documents, start=1)
    ]
    return "\n".join([documents_header(nonce), *fences])
