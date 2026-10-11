"""RAG service: index documents and retrieve cited chunks (the knowledge path).

Indexing: read text, Markdown, code or PDF -> heading/sentence-aware chunks, each with
the heading path it sits under (Markdown's headings; other text has none) -> embed (the
shared GPU embedder), path and text -> store, where a trigger also indexes it for
keyword search. Retrieval: the nearest chunks by meaning and the best keyword matches,
fused by rank (`fuse`), then reranked by a cross-encoder (`rag_rerank.py`), each
carrying its source title, ordinal and heading path for citation. Knowledge stays
separate from conversational memory. List-changing operations emit `rag.updated`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Protocol

from app.core.config_memory import MemorySettings
from app.core.event_bus import EventBus
from app.schemas.events import Event
from app.services.memory.chunking import PATH_SEPARATOR, Chunk, chunk_text
from app.services.memory.models import RagDocument, RetrievedChunk
from app.services.memory.rag_fts import identifiers
from app.services.memory.rag_rerank import Reranking
from app.services.memory.rag_store import RagStore
from app.services.memory.sensitive import is_sensitive
from app.telemetry import current_perf

# Source and config files are plain text, so they are read like .txt. The chat surface
# accepts them as attachments; refusing a .py while accepting a .txt with the same
# bytes would be an arbitrary distinction.
CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".css",
        ".html",
        ".sql",
        ".sh",
        ".rs",
        ".go",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".csv",
        ".log",
        ".rst",
    }
)
_TEXT_SUFFIXES = {".txt", ".text"} | CODE_SUFFIXES
_MARKDOWN_SUFFIXES = {".md", ".markdown"}
# A document's text, however it arrives, holds to an upload's limit (`api/uploads.py`):
# pasted is only another way of sending one.
MAX_TEXT_BYTES = 25 * 1024 * 1024
TEXT_TOO_LONG = "That text is longer than I can take (25 MB)."


class _Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class RagError(ValueError):
    """A user-facing RAG failure: `code` for the app, the message in plain words."""

    def __init__(self, message: str, code: str = "unreadable_file") -> None:
        super().__init__(message)
        self.code = code


class RagService:
    """Index sourced documents and retrieve cited chunks."""

    def __init__(
        self,
        store: RagStore,
        embedder: _Embedder,
        bus: EventBus,
        settings: MemorySettings,
        reranking: Reranking | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._bus = bus
        self._s = settings
        self._reranking = reranking

    async def init_schema(self) -> None:
        await self._store.init_schema()

    async def index_text(
        self, title: str, text: str, *, source_path: str | None = None, mime: str = "text/plain"
    ) -> RagDocument:
        # Refused before any work: chunking and embedding scale with the text, and a
        # pasted document had no limit at all.
        if len(text) > MAX_TEXT_BYTES or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise RagError(TEXT_TOO_LONG, "text_too_long")
        started = time.perf_counter()
        # Chunking is pure CPU and slow on long text, and so is the secrets check on
        # every chunk. Run both off the event loop so indexing can't stall audio, VAD,
        # barge-in and /health for seconds. Only Markdown has headings.
        chunked = await asyncio.to_thread(
            chunk_text,
            text,
            title=title,
            target_chars=self._s.chunk_target_chars,
            overlap_chars=self._s.chunk_overlap_chars,
            markdown=mime == "text/markdown",
        )
        if not chunked:
            raise RagError("That document has no readable text to index.", "empty_document")
        chunks = await asyncio.to_thread(_without_secrets, chunked)
        if not chunks:
            raise RagError(
                "That document looks like it holds credentials, so I didn't index it.",
                "sensitive_content",
            )
        # Embed first, then write the document and its chunks together, so nothing that
        # stops the work can leave a listed document with no chunks. The path is embedded
        # with the text: "Bake at 230 °C" is the focaccia's only because its heading says so.
        embeddings = await self._embedder.embed([_embedded(chunk) for chunk in chunks])
        items = [
            (ordinal, chunk.text, chunk.path, embeddings[ordinal])
            for ordinal, chunk in enumerate(chunks)
        ]
        document_id = await self._store.add_document(title, source_path, mime, items)
        current_perf().log(
            "rag.index",
            (time.perf_counter() - started) * 1000.0,
            chunks=len(chunks),
            chars=len(text),
            mime=mime,
        )
        await self._emit_updated()
        document = await self._store.get_document(document_id)
        if document is None:  # pragma: no cover - just inserted
            raise RagError("Indexed document vanished.")
        return document

    async def index_file(
        self, path: str | Path, *, title: str | None = None, source_path: str | None = None
    ) -> RagDocument:
        """Index a file on disk. Its own path is recorded only if the caller passes it.

        Uploads arrive as temp files, and a temp path on Windows sits under the user's
        profile, so storing it would put the account name in the database and the API.
        """
        file_path = Path(path)
        # pypdf extraction can take seconds of CPU per document; keep it off the loop.
        text, mime = await asyncio.to_thread(_read_file, file_path)
        try:
            return await self.index_text(
                title or file_path.stem, text, source_path=source_path, mime=mime
            )
        except RagError as exc:
            if exc.code != "text_too_long":
                raise
            # Under the upload limit, over the text one: a PDF's text, or bytes that
            # weren't text, each read as a three-byte replacement character.
            raise RagError("That file holds more text than I can take (25 MB).", exc.code) from exc

    async def retrieve(
        self, query: str, *, k: int | None = None, document_ids: list[int] | None = None
    ) -> list[RetrievedChunk]:
        """Relevant chunks, best first. `document_ids` narrows the search to a project's
        documents.

        The first `rag_rerank_depth` candidates are reranked (`rag_rerank.py`), however
        few are asked for, so a turn's five and the evaluation's ten are the same
        ranking cut at different places.
        """
        query = query.strip()
        if not query:
            return []
        k = k or self._s.rag_top_k
        vector = await self._embedder.embed_query(query)
        reranking = self._reranking if self._s.rag_rerank_depth > 0 else None
        pool = max(k, self._s.rag_rerank_depth) if reranking else k
        found = await self._candidates(query, vector, pool, document_ids)
        if reranking is not None:
            found = await reranking.rerank(query, found)
        return found[:k]

    async def _candidates(
        self, query: str, vector: list[float], k: int, document_ids: list[int] | None
    ) -> list[RetrievedChunk]:
        """The nearest chunks by meaning. A question that names an identifier (a code,
        a part number, an address: `rag_fts.identifiers`) adds the chunks sharing the
        most telling words, each search `rag_keyword_depth` deep, fused by rank. Only
        then: fusing keywords into every question took the right passage off first
        place for paraphrased ones (85% first to 73% on the retrieval evaluation, in a
        run not kept), while for identifiers it brings in answers the meaning search
        leaves out."""
        depth = self._s.rag_keyword_depth
        if depth <= 0 or not identifiers(query):
            return await self._store.knn_chunks(vector, k, document_ids=document_ids)
        depth = max(depth, k)
        nearest = await self._store.knn_chunks(vector, depth, document_ids=document_ids)
        telling = await self._store.keyword_chunks(query, vector, depth, document_ids=document_ids)
        return fuse([nearest, telling])[:k]

    async def list_documents(self) -> list[RagDocument]:
        return await self._store.list_documents()

    async def has_documents(self) -> bool:
        return await self._store.has_documents()

    async def delete(self, document_id: int) -> None:
        await self._store.delete_document(document_id)
        await self._emit_updated()

    async def _emit_updated(self) -> None:
        await self._bus.publish(Event(type="rag.updated", source="rag", payload={}))


def _without_secrets(chunks: list[Chunk]) -> list[Chunk]:
    """The chunks without the ones that look like they hold a secret.

    Memory refuses to store secrets, and indexing a .env or a credentials note would
    put them into prompts anyway. Only the sensitive chunks go, so one stray line
    doesn't reject the whole file. A heading that looks like one leaves the path, which
    would carry it into the embedding and the label of every chunk below it; the chunks
    stay (a guide under "## Passwords 101" is not a secret).
    """
    return [_unflagged(chunk) for chunk in chunks if not is_sensitive(chunk.text)]


def _unflagged(chunk: Chunk) -> Chunk:
    if not is_sensitive(chunk.path):
        return chunk
    parts = [part for part in chunk.path.split(PATH_SEPARATOR) if not is_sensitive(part)]
    path = PATH_SEPARATOR.join(parts)
    return Chunk(chunk.text, "" if is_sensitive(path) else path)


def _embedded(chunk: Chunk) -> str:
    return f"{chunk.path}\n\n{chunk.text}" if chunk.path else chunk.text


# Reciprocal rank fusion's damping. At 60 the first place is worth 1/61 and the tenth
# 1/70: a chunk both searches rank well beats one that only one search puts first.
_RRF_K = 60


def fuse(ranked: list[list[RetrievedChunk]]) -> list[RetrievedChunk]:
    """Several ranked lists as one, by reciprocal rank fusion: each chunk scores the sum
    of 1 / (60 + its rank) in every list it is in.

    Ranks, never the scores: a cosine and a BM25 value have no common scale, and any
    scheme that makes them comparable needs a calibration the next library undoes. Ties
    keep the order the chunks were first met in, the first list's first.
    """
    scores: dict[int, float] = {}
    first: dict[int, RetrievedChunk] = {}
    for hits in ranked:
        for rank, hit in enumerate(hits, 1):
            scores[hit.chunk.id] = scores.get(hit.chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
            first.setdefault(hit.chunk.id, hit)
    order = sorted(first, key=lambda chunk_id: -scores[chunk_id])  # stable: ties keep order
    return [first[chunk_id] for chunk_id in order]


def _read_file(path: Path) -> tuple[str, str]:
    # "utf-8-sig": a byte-order mark kept in front of "# Family recipes" would stop the
    # line reading as a heading.
    suffix = path.suffix.lower()
    if suffix in _MARKDOWN_SUFFIXES:
        return path.read_text(encoding="utf-8-sig", errors="replace"), "text/markdown"
    if suffix in _TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8-sig", errors="replace"), "text/plain"
    if suffix == ".pdf":
        return _read_pdf(path), "application/pdf"
    raise RagError(f"Unsupported file type: {suffix or 'unknown'}", "unsupported_file")


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
