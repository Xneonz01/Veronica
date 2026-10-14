"""Shared test doubles: fake adapters, WebSocket, detector and metrics sink.

There are also builders that wire the real memory, RAG, kernel, persona and chat
stores onto a temp database with a deterministic fake embedder. Together they let
tests exercise the real session, route and event-bus wiring without loading any
model. Orchestration tests drive a session through ``FakeWS``; the transport test
puts a fake ``VoiceRuntime`` behind a real TestClient socket.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.adapters.llm.ollama_errors import (
    OllamaModelMissingError,
    OllamaModelNoToolsError,
    OllamaUnreachableError,
)
from app.adapters.llm.types import LlmChunk
from app.capabilities import register_capabilities
from app.core.config import Settings
from app.core.config_capabilities import CapabilitySettings
from app.core.config_memory import MemorySettings
from app.core.data_key import key_path, open_key
from app.core.event_bus import EventBus, InProcessEventBus
from app.kernel.runtime import KernelRuntime, build_kernel_runtime
from app.services.chat.projects import ProjectStore
from app.services.chat.store import ChatStore
from app.services.memory import at_rest, cipher
from app.services.memory.assembler import ContextAssembler
from app.services.memory.call_queue import CallQueue
from app.services.memory.learning import LearningController
from app.services.memory.rag import RagService
from app.services.memory.rag_store import RagStore
from app.services.memory.retriever import MemoryRetriever
from app.services.memory.runtime import MemoryRuntime
from app.services.memory.service import MemoryService
from app.services.memory.store import MemoryStore
from app.services.persona import PersonaService
from app.services.voice.audio import Transcript
from app.services.voice.runtime import VoiceRuntime
from app.services.voice.turn import TurnEvent


class FakeWS:
    """Captures what the session sends; stands in for a Starlette WebSocket."""

    def __init__(self) -> None:
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []
        self.sent: list[str | bytes] = []  # both, in the order they went out

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)
        self.sent.append(data)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        self.sent.append(text)


class FakeManager:
    async def set_mode(self, _mode: Any, *, load: bool = True) -> None:
        return None

    def to_load(self, _mode: Any) -> set[str]:
        return set()  # every model a mode needs is in memory already


class FakeVoiceService:
    active_voice_id = "veronica-default"


class FakeDetector:
    """Emits scripted TurnEvents, one list per process_audio call."""

    def __init__(self, script: list[list[TurnEvent]], *, silence_ms: float = 0.0) -> None:
        self._script = script
        self._i = 0
        self._silence_ms = silence_ms  # reported as the silence after every utterance

    def process_audio(self, _samples: Any) -> list[TurnEvent]:
        if self._i < len(self._script):
            events = self._script[self._i]
            self._i += 1
            return events
        return []

    def trailing_silence_ms(self) -> float:
        return self._silence_ms


class FakeSTT:
    def __init__(self, partials: list[str], final: str | None, *, boom: bool = False) -> None:
        self._partials = partials
        self._final = final
        self._boom = boom  # raise mid-stream to exercise STT-failure handling

    def stream_transcripts(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        async def gen() -> AsyncIterator[Transcript]:
            async for _ in audio:  # drain the utterance so the sentinel lands
                pass
            if self._boom:
                raise RuntimeError("stt boom")
            for text in self._partials:
                yield Transcript(text, is_final=False)
            if self._final is not None:
                yield Transcript(self._final, is_final=True)

        return gen()


class Warms:
    """`warm` as the real client has it: one short request through `stream_chat`."""

    async def warm(self, model: Any = None) -> None:
        async for _ in self.stream_chat([{"role": "user", "content": "Ready"}], model=model):  # type: ignore[attr-defined]
            break


class FakeLLM(Warms):
    def __init__(
        self,
        tokens: list[str],
        *,
        unreachable: bool = False,
        missing_model: str | None = None,
        refusal: Exception | None = None,
        crash: bool = False,
        block: asyncio.Event | None = None,
        trace: list[Any] | None = None,
        seen: list[Any] | None = None,
    ) -> None:
        self._tokens = tokens
        self._unreachable = unreachable
        self._missing_model = missing_model  # Ollama doesn't have it (a 404)
        self._refusal = refusal  # any other refusal of Ollama's (`ollama_errors`)
        self._crash = crash  # raise a non-Ollama error mid-reply (after tokens stream)
        self._block = block
        self._trace = trace
        self._seen = seen  # captures each call's `messages` (to assert injected context)

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        *,
        model: Any = None,
        keep_alive: Any = None,
        temperature: Any = None,
    ) -> AsyncIterator[LlmChunk]:
        if self._seen is not None:
            self._seen.append(messages)
        if self._unreachable:
            raise OllamaUnreachableError("ollama down")
        if self._missing_model is not None:
            raise OllamaModelMissingError(self._missing_model)
        # As Ollama does: a model without tools is refused only when tools are offered,
        # and a call's warm-up offers none.
        if self._refusal is not None and (
            tools or not isinstance(self._refusal, OllamaModelNoToolsError)
        ):
            raise self._refusal
        for token in self._tokens:
            yield LlmChunk(content=token)
        if self._block is not None:  # hold the reply open for barge-in tests
            await self._block.wait()
        if self._crash:  # a generic failure deep in the reply pipeline
            raise RuntimeError("reply boom")
        if self._trace is not None:
            self._trace.append("llm_done")
        yield LlmChunk(done=True)

    async def aclose(self) -> None:
        return None


class ScriptedLLM(Warms):
    """An LLM whose successive stream_chat calls follow a script (tokens or a tool call).

    Each script step is ``{"tokens": [...]}`` (a streamed reply) or
    ``{"tool_calls": [{"function": {"name", "arguments"}}]}`` (the model calling a
    tool). Captures the messages it was given each call (to assert injected context).

    ``{"hang": True}`` yields its tokens and then never finishes, simulating a
    generation still in flight so tests can stop or interrupt it.
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        *,
        model: Any = None,
        keep_alive: Any = None,
        temperature: Any = None,
    ) -> AsyncIterator[LlmChunk]:
        self.calls.append(list(messages))
        step = self._script.pop(0) if self._script else {}
        for token in step.get("tokens", []):
            yield LlmChunk(content=token)
        if step.get("hang"):
            await asyncio.Event().wait()  # cancelled by the caller, never on its own
        yield LlmChunk(done=True, tool_calls=step.get("tool_calls"))


class FakeTTS:
    def __init__(self, *, trace: list[Any] | None = None) -> None:
        self._trace = trace

    async def activate_voice(self, _voice_id: str, *, load: bool = True) -> None:
        return None

    def synthesize_stream(self, text: str, _voice_id: str) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            if self._trace is not None:
                self._trace.append(("synth", text))
            yield text.encode()

        return gen()


class ListMetricsSink:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def write(self, metrics: Any) -> None:
        self.rows.append(metrics.as_dict())


def build_fake_runtime(
    *,
    stt: FakeSTT,
    tts: FakeTTS,
    llm: FakeLLM,
    manager: FakeManager | None = None,
    voice_service: FakeVoiceService | None = None,
) -> VoiceRuntime:
    """Assemble a VoiceRuntime from fakes — no real model or network is touched."""
    return VoiceRuntime(
        manager=manager or FakeManager(),  # type: ignore[arg-type]
        stt=stt,  # type: ignore[arg-type]
        tts=tts,  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        voice_service=voice_service or FakeVoiceService(),  # type: ignore[arg-type]
    )


class FakeEmbedder:
    """Deterministic keyword-axis embeddings for memory tests (dim 8, normalized).

    Text projects onto a few topic axes by keyword, so cosine similarity between a
    query and a memory is predictable — which lets the relevance-gate test assert
    that an off-topic memory is dropped no matter how recent/important it is.
    """

    DIM = 8
    _AXES = {
        "sourdough": 0,
        "starter": 0,
        "dough": 0,
        "baking": 0,
        "feed": 0,
        "mom": 1,
        "mother": 1,
        "family": 1,
        "weather": 2,
        "cold": 2,
        "rain": 2,
        "coffee": 3,
        "espresso": 3,
        "latte": 3,
    }

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.DIM
        for token in re.findall(r"\w+", text.lower()):
            axis = self._AXES.get(token)
            if axis is not None:
                vec[axis] += 1.0
        norm = math.sqrt(sum(value * value for value in vec)) or 1.0
        return [value / norm for value in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


async def build_fake_memory_stack(
    db_dir: Path, bus: EventBus, settings: MemorySettings | None = None
) -> tuple[MemoryStore, MemoryService, MemoryRetriever]:
    """Store/service/retriever wired with the FakeEmbedder (dim 8) under db_dir."""
    config = settings or MemorySettings()
    store = MemoryStore(db_dir / "memory.db", FakeEmbedder.DIM)
    await store.init()
    OPEN_STORES.append(store)  # closed after a failed test too (see OPEN_STORES)
    embedder = FakeEmbedder()
    service = MemoryService(store, embedder, bus, config)
    retriever = MemoryRetriever(store, embedder, config)
    return store, service, retriever


async def build_fake_rag(
    store: MemoryStore, bus: EventBus, settings: MemorySettings | None = None
) -> RagService:
    """A RagService on an already-initialized fake memory store (FakeEmbedder, dim 8)."""
    config = settings or MemorySettings()
    rag = RagService(RagStore(store, FakeEmbedder.DIM), FakeEmbedder(), bus, config)
    await rag.init_schema()
    return rag


def build_fake_persona(tmp_path: Path, bus: EventBus | None = None) -> PersonaService:
    """A persona service writing to a temp file, so tests never touch data/persona.json."""
    return PersonaService(tmp_path / "persona.json", bus or InProcessEventBus())


async def build_fake_kernel_runtime(
    memory: MemoryRuntime,
    bus: EventBus,
    *,
    file_roots: list[str] | None = None,
    confirm_timeout_s: float | None = None,
    policy: dict[str, str] | None = None,
) -> KernelRuntime:
    """A real kernel on the fake memory runtime, with capabilities registered.

    `file_roots` defaults to empty, so a test that doesn't opt in cannot touch the
    filesystem; capability tests pass their own tmp_path.
    """
    settings = Settings()
    settings.capabilities = CapabilitySettings(file_roots=file_roots or [], policy=policy or {})
    if confirm_timeout_s is not None:  # a spoken-confirm test cannot wait 45 seconds
        settings.capabilities.confirm_timeout_s = confirm_timeout_s
    runtime = build_kernel_runtime(settings, bus, memory.store)
    register_capabilities(runtime.registry, settings, memory=memory)
    await runtime.init()
    return runtime


# Every store the fakes open, so a test that fails before closing its own doesn't hang
# the run: aiosqlite's worker thread isn't a daemon, and an open store keeps the process
# from exiting. `conftest.py` closes what is left after each test.
OPEN_STORES: list[MemoryStore] = []


async def build_fake_memory_runtime(
    db_dir: Path, bus: EventBus, settings: MemorySettings | None = None, key: bytes | None = None
) -> MemoryRuntime:
    """A real MemoryRuntime wired with the FakeEmbedder (dim 8) for conversation tests; its
    file plain, or encrypted with `key`."""
    config = settings or MemorySettings()
    embedder = FakeEmbedder()
    store = MemoryStore(db_dir / "memory.db", FakeEmbedder.DIM, key=key)
    await store.init()
    OPEN_STORES.append(store)
    service = MemoryService(store, embedder, bus, config)
    retriever = MemoryRetriever(store, embedder, config)
    assembler = ContextAssembler(retriever)
    learning = LearningController(service, bus, config, store)
    rag = RagService(RagStore(store, FakeEmbedder.DIM), embedder, bus, config)
    await rag.init_schema()
    calls = CallQueue(store)
    await calls.init_schema()
    return MemoryRuntime(
        store=store,
        service=service,
        retriever=retriever,
        assembler=assembler,
        learning=learning,
        rag=rag,
        embedder=embedder,
        calls=calls,
    )


async def build_fake_chat_store(store: MemoryStore) -> ChatStore:
    """A conversation store on the test's memory connection, schema created."""
    chat_store = ChatStore(store)
    await chat_store.init_schema()
    return chat_store


async def build_fake_project_store(store: MemoryStore) -> ProjectStore:
    """A project store on the test's connection, schema created.

    Init order matters: its migration adds a column to chat_conversations, so
    ChatStore's schema must exist first.
    """
    project_store = ProjectStore(store)
    await project_store.init_schema()
    return project_store


def open_memory_file(path: Path) -> Any:
    """A blocking connection to a memory file as a start leaves it: through the key beside
    it once it is encrypted (`core/data_key.py`), plain before."""
    key = open_key(key_path(path), encrypted=True) if at_rest.holds_encrypted(path) else None
    return cipher.open_sync(path, key)
