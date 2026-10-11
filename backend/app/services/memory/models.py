"""Pydantic row models and enums for the memory store.

A memory is conversational knowledge about the user/world (facts, preferences,
episodic notes, session summaries) plus a Tier-1 key/value profile. RAG document
chunks are a separate path and never share these tables.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class MemoryKind(StrEnum):
    profile = "profile"  # Tier-1 key/value (name, pronouns, ...) — never vector-retrieved
    fact = "fact"
    preference = "preference"
    episodic = "episodic"
    summary = "summary"  # a session summary


class LearningMode(StrEnum):
    proactive = "proactive"  # auto-store high-confidence facts; queue the rest for review
    review_only = "review_only"  # store nothing active without explicit approval


class AddressByName(StrEnum):
    """How freely Veronica may use the user's name — a Tier-1 slot, default sparingly."""

    never = "never"
    sparingly = "sparingly"
    often = "often"


def _now() -> datetime:
    return datetime.now(UTC)


# The longest memory kept, typed on the Memory page or told to her. Retrieval counts
# about four characters a token against a 512-token block, so a memory past 2,048
# characters would be stored and never reach a prompt.
MAX_MEMORY_CHARS = 2000


class MemoryCreate(BaseModel):
    """Input to create a memory (the store assigns id/timestamps/normalized form)."""

    kind: MemoryKind
    content: str
    importance: float = 0.5
    confidence: float = 1.0
    pinned: bool = False
    source: str = "system"
    key: str | None = None  # only meaningful for kind=profile
    supersedes_id: int | None = None
    pending: bool = False  # a learning candidate awaiting approval (not retrievable yet)


class Memory(BaseModel):
    """A persisted memory row."""

    id: int
    kind: MemoryKind
    content: str
    normalized_content: str
    importance: float
    confidence: float
    pinned: bool
    source: str
    key: str | None = None
    session_id: int | None = None
    supersedes_id: int | None = None
    pending: bool = False  # awaiting approval (a learning candidate); not retrievable
    archived: bool = False
    access_count: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_accessed_at: datetime | None = None


class RetrievedMemory(BaseModel):
    """A memory the retriever selected, with its score breakdown (debuggable)."""

    memory: Memory
    score: float
    semantic: float  # rescaled 0..1
    lexical: float  # token overlap 0..1


class RetrievedContext(BaseModel):
    """The typed result of a retrieval: Tier-1 profile + gated Tier-2/3 items."""

    profile: dict[str, str] = Field(default_factory=dict)
    items: list[RetrievedMemory] = Field(default_factory=list)
    tiers_used: list[str] = Field(default_factory=list)


# -- RAG / knowledge (separate path from conversational memory) --


class RagDocument(BaseModel):
    """A source document indexed for retrieval-augmented answers."""

    id: int
    title: str
    mime: str
    source_path: str | None = None
    chunk_count: int = 0
    added_at: datetime = Field(default_factory=_now)


class RagChunk(BaseModel):
    """One indexed passage of a document."""

    id: int
    document_id: int
    ordinal: int
    text: str
    # The headings it sits under, the document's title first ("recipes › Breads ›
    # Focaccia"); empty for a chunk indexed before 0.22.0.
    heading_path: str = ""
    created_at: datetime = Field(default_factory=_now)


class RetrievedChunk(BaseModel):
    """A retrieved chunk with its source title + ordinal for citation."""

    chunk: RagChunk
    document_title: str
    distance: float  # cosine distance (lower = closer)
    # The cross-encoder's score when the search was reranked (higher = better answer);
    # None when it wasn't, and the gate falls back on the distance.
    relevance: float | None = None


# -- Controlled learning (session-end extraction) --


class ExtractedFact(BaseModel):
    """A candidate fact the session-end extractor proposes."""

    content: str
    confidence: float = 0.5
    kind: MemoryKind = MemoryKind.fact

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        # The model supplies this; clamp (don't reject) so one bad value never drops
        # the whole extraction, and an out-of-range confidence never reaches the UI.
        return max(0.0, min(1.0, value))


class Extraction(BaseModel):
    """The learning extractor's structured output for one session."""

    profile: dict[str, str] = Field(default_factory=dict)
    facts: list[ExtractedFact] = Field(default_factory=list)
    malformed: int = 0  # facts dropped because they couldn't be read (`parse_extraction`)

    @field_validator("profile", mode="before")
    @classmethod
    def _drop_empty_slots(cls, value: object) -> object:
        # A model fills a slot it has nothing for with null. Drop that entry, not the
        # whole extraction, as confidence is clamped rather than rejected.
        if not isinstance(value, dict):
            return value
        return {
            key: text.strip()
            for key, text in value.items()
            if isinstance(key, str) and isinstance(text, str) and text.strip()
        }
