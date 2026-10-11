"""Wiring for the memory stack: embedder + store + service + retriever + RAG.

The embedding model registers on the shared GPU Model Manager passed in from the
voice runtime, so every model goes through one manager. Memory and the RAG/knowledge
path share one SQLite connection (separate tables). Construction is cheap (the model
lazy-loads on first use); ``init()`` opens the store and makes the RAG tables, and is
awaited in the app lifespan. The file's upgrade to this build waits until every store on
it has made its tables (`migrations.upgrade`), and no store writes a row before it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.embeddings.e5_embedder import EMBED_MODEL_ID, E5Embedder, embedding_loader
from app.adapters.rerank.cross_encoder import open_reranker
from app.core.config import Settings
from app.core.event_bus import EventBus
from app.core.gpu_manager import CudaGpuModelManager, GpuMode, gib
from app.services.memory.assembler import ContextAssembler
from app.services.memory.call_queue import CallQueue
from app.services.memory.learning import LearningController
from app.services.memory.rag import RagService
from app.services.memory.rag_rerank import Reranking
from app.services.memory.rag_store import RagStore
from app.services.memory.retriever import MemoryRetriever
from app.services.memory.service import MemoryService
from app.services.memory.store import MemoryStore


@dataclass(slots=True)
class MemoryRuntime:
    """The constructed memory + RAG components, shared via ``app.state``."""

    store: MemoryStore
    service: MemoryService
    retriever: MemoryRetriever
    assembler: ContextAssembler
    learning: LearningController
    rag: RagService
    embedder: E5Embedder
    calls: CallQueue  # finished calls, until learning has read them

    async def init(self) -> None:
        """Open the store (aiosqlite + sqlite-vec) and make the RAG and call tables;
        the upgrade comes after every store's tables (`migrations.upgrade`)."""
        await self.store.init()
        await self.rag.init_schema()
        await self.calls.init_schema()


def build_memory_runtime(
    settings: Settings, bus: EventBus, manager: CudaGpuModelManager, key: bytes | None
) -> MemoryRuntime:
    """Register the embedder on the shared manager and build the memory + RAG stack, on
    a file keyed with the data key (`core/data_key.py`; None: plain)."""
    manager.register(
        EMBED_MODEL_ID,
        embedding_loader(settings.memory),
        {GpuMode.idle, GpuMode.conversation},
        gib(settings.memory.embedding_vram_gb),
    )
    embedder = E5Embedder(manager)
    store = MemoryStore(settings.memory.db_path, settings.memory.embedding_dim, key=key)
    service = MemoryService(store, embedder, bus, settings.memory)
    retriever = MemoryRetriever(store, embedder, settings.memory)
    assembler = ContextAssembler(retriever)
    learning = LearningController(service, bus, settings.memory, store)
    rag = RagService(
        RagStore(store, settings.memory.embedding_dim),
        embedder,
        bus,
        settings.memory,
        Reranking(open_reranker),  # a CPU model, opened on first use, not a GPU one
    )
    return MemoryRuntime(
        store=store,
        service=service,
        retriever=retriever,
        assembler=assembler,
        learning=learning,
        rag=rag,
        embedder=embedder,
        calls=CallQueue(store),
    )
