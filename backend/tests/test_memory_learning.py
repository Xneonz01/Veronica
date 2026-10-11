"""The controlled-learning loop: proactive auto-store, review queue, sensitivity, correction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.adapters.llm.ollama_errors import OllamaModelMissingError, OllamaUnreachableError
from app.adapters.llm.types import LlmChunk
from app.core.config_memory import MemorySettings
from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.memory.extraction import kept_profile, parse_extraction, session_transcript
from app.services.memory.learning import History, LearningController
from app.services.memory.models import (
    AddressByName,
    ExtractedFact,
    Extraction,
    LearningMode,
    MemoryCreate,
    MemoryKind,
)
from tests.fakes import FakeLLM, build_fake_memory_runtime


class _RecordingLLM:
    """Captures the keep_alive and temperature the extractor passes."""

    def __init__(self) -> None:
        self.keep_alive: object = "unset"
        self.temperature: object = "unset"

    async def stream_chat(
        self, messages, tools=None, *, model=None, keep_alive=None, temperature=None
    ):  # type: ignore[no-untyped-def]
        self.keep_alive = keep_alive
        self.temperature = temperature
        yield LlmChunk(content='{"profile": {}, "facts": []}')
        yield LlmChunk(done=True)


def said(text: str) -> list[dict[str, str]]:
    """A session in which the user said `text`."""
    return [{"role": "user", "content": text}]


async def learn(learning: LearningController, history: History, llm: Any, model: str) -> bool:
    """What the learning worker does with one part it read: extract, then file. False
    when the answer couldn't be read."""
    extraction = await learning.extract(history, llm, model)
    if extraction is None:
        return False
    await learning.file(extraction, history)
    return True


async def test_extractor_leaves_the_model_loaded(tmp_path: Path) -> None:
    # One model serves chat, calls and learning, so releasing it after an extraction
    # only made the next turn wait for it to load again.
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = _RecordingLLM()
    await learn(runtime.learning, said("hi"), llm, "qwen3.5:9b")  # type: ignore[arg-type]
    assert llm.keep_alive is None  # the configured keep_alive applies
    await runtime.store.close()


async def test_learning_asks_at_its_own_temperature(tmp_path: Path) -> None:
    """Bug 48: at chat's 0.7, 8 extractions in 60 of plain facts came back empty; at 0.2,
    1 in 60 (mutation: send none, so chat's applies)."""
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = _RecordingLLM()
    await learn(runtime.learning, said("I work nights at the hospital."), llm, "m")  # type: ignore[arg-type]
    assert llm.temperature == MemorySettings().learning_temperature == 0.2
    await runtime.store.close()


def test_extracted_fact_clamps_confidence() -> None:
    # The model supplies confidence; an out-of-range value is clamped, not rejected,
    # so it never drops the extraction or shows "140%" in the UI.
    assert ExtractedFact(content="x", confidence=1.4).confidence == 1.0
    assert ExtractedFact(content="x", confidence=-0.2).confidence == 0.0
    assert ExtractedFact(content="x", confidence=0.7).confidence == 0.7


def _extraction_llm(
    profile: dict[str, str] | None = None, facts: list[dict[str, Any]] | None = None
) -> FakeLLM:
    """A fake LLM whose single reply is the extractor's JSON."""
    return FakeLLM([json.dumps({"profile": profile or {}, "facts": facts or []})])


async def test_the_extraction_says_whether_it_could_read_the_answer(tmp_path: Path) -> None:
    """The learning worker reads less next time on None, and waits for a model that is
    away (`chat/learning_worker.py`); nothing to learn is not a failure (mutations:
    None for an answer that was read; swallow the model being away)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    learning = mem.learning

    assert await learning.extract(said("hello"), _extraction_llm(), "m") == Extraction()
    assert await learning.extract(said("hello"), FakeLLM(["I'd rather not say."]), "m") is None
    with pytest.raises(OllamaUnreachableError):
        await learning.extract(said("hello"), FakeLLM([], unreachable=True), "m")
    seen: list[Any] = []
    her_words_only = [{"role": "assistant", "content": "Hello!"}]
    assert await learning.extract(her_words_only, FakeLLM([], seen=seen), "m") == Extraction()
    assert seen == []  # the model wasn't asked
    await mem.store.close()


async def test_a_model_that_isnt_installed_is_raised_not_read_as_an_unreadable_answer(
    tmp_path: Path,
) -> None:
    """The chat worker waits for it rather than halving and skipping what was said
    (mutation: let `_ask` swallow it as None)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())

    with pytest.raises(OllamaModelMissingError):
        await mem.learning.extract(said("hello"), FakeLLM([], missing_model="m"), "m")
    await mem.store.close()


async def test_filing_the_same_answer_twice_files_nothing_twice(tmp_path: Path) -> None:
    """A retry, or a start after a quit mid-filing, reads the same turns again. A
    candidate already waiting and a profile value she already has are not queued or
    announced again (mutations: skip either check)."""
    bus = InProcessEventBus()
    learned: list[Event] = []

    async def capture(event: Event) -> None:
        learned.append(event)

    bus.subscribe("memory.learned", capture)
    mem = await build_fake_memory_runtime(tmp_path, bus)
    answer = json.dumps(
        {"profile": {"name": "Rowan"}, "facts": [{"content": "Might like jazz", "confidence": 0.3}]}
    )
    for _ in range(2):
        heard = said("My name is Rowan, and I might like jazz")
        assert await learn(mem.learning, heard, FakeLLM([answer]), "m") is True

    assert [m.content for m in await mem.service.list(pending=True)] == ["Might like jazz"]
    assert len(learned) == 2  # the name and the candidate, once each
    await mem.store.close()


async def test_a_fact_that_cannot_be_stored_active_waits_for_review(tmp_path: Path) -> None:
    """Storing it active needs the memory model; when that fails the fact goes to review
    rather than being lost (mutation: let the error through)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())

    class _Broken:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("CUDA out of memory")

    mem.service._embedder = _Broken()  # type: ignore[assignment]
    llm = _extraction_llm(facts=[{"content": "User has a cat named Max", "confidence": 0.95}])

    assert await learn(mem.learning, said("I have a cat named Max"), llm, "m") is True

    pending = await mem.service.list(pending=True)
    assert [m.content for m in pending] == ["User has a cat named Max"]
    await mem.store.close()


async def test_one_item_that_fails_to_file_leaves_the_rest_filed(tmp_path: Path) -> None:
    """Items are filed one by one (mutation: let one error end the filing)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database is locked")

    mem.service.set_profile = refuse  # type: ignore[method-assign,assignment]
    llm = _extraction_llm(
        profile={"name": "Rowan"},
        facts=[{"content": "User has a cat named Max", "confidence": 0.95}],
    )

    heard = said("My name is Rowan and I have a cat named Max")
    assert await learn(mem.learning, heard, llm, "m") is True

    assert [m.content for m in await mem.service.list()] == ["User has a cat named Max"]
    await mem.store.close()


async def test_session_learns_share_one_coalesce_id(tmp_path: Path) -> None:
    # A whole session's learns carry one coalesce_id so the UI collapses the burst.
    bus = InProcessEventBus()
    learned: list[Event] = []

    async def capture(event: Event) -> None:
        learned.append(event)

    bus.subscribe("memory.learned", capture)
    runtime = await build_fake_memory_runtime(tmp_path, bus)
    llm = _extraction_llm(
        facts=[
            {"content": "User likes tea", "confidence": 0.9, "kind": "preference"},
            {"content": "User has a cat", "confidence": 0.9, "kind": "fact"},
        ]
    )
    await learn(runtime.learning, said("hello"), llm, "model")
    assert len(learned) == 2
    assert len({e.payload["coalesce_id"] for e in learned}) == 1
    assert all(e.payload["action"] == "added" and "summary" in e.payload for e in learned)
    await runtime.store.close()


async def test_proactive_autostores_high_confidence_fact(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    learned: list[Event] = []

    async def collect(event: Event) -> None:
        learned.append(event)

    bus.subscribe("memory.learned", collect)
    mem = await build_fake_memory_runtime(tmp_path, bus)
    llm = _extraction_llm(facts=[{"content": "Has a cat named Max", "confidence": 0.95}])

    await learn(mem.learning, said("hello"), llm, "fake-model")
    active = await mem.service.list()
    assert any("cat named Max" in m.content and not m.pending for m in active)
    assert learned and learned[-1].payload["active"] is True  # announced, never silent
    await mem.store.close()


async def test_a_fact_it_cannot_read_leaves_the_rest_learned(tmp_path: Path) -> None:
    """One fact of a kind the product doesn't have took every fact of the session with it
    (bug 24; mutation: validate the reply as a whole)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = _extraction_llm(
        facts=[
            {"content": "User has a cat named Max", "confidence": 0.95},
            {"content": "User swims well", "confidence": 0.9, "kind": "skill"},
        ]
    )

    await learn(mem.learning, said("hello"), llm, "fake-model")

    assert [m.content for m in await mem.service.list()] == ["User has a cat named Max"]
    await mem.store.close()


async def test_proactive_queues_low_confidence_fact(tmp_path: Path) -> None:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = _extraction_llm(facts=[{"content": "Might like jazz", "confidence": 0.3}])

    await learn(mem.learning, said("hello"), llm, "fake-model")
    pending = await mem.service.list(pending=True)
    assert any("jazz" in m.content for m in pending)
    ctx = await mem.retriever.retrieve("do I like jazz music")  # candidates aren't retrievable
    assert all("jazz" not in item.memory.content for item in ctx.items)
    await mem.store.close()


async def test_review_only_queues_everything(tmp_path: Path) -> None:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.learning.set_mode(LearningMode.review_only)
    llm = _extraction_llm(
        profile={"name": "Rowen"}, facts=[{"content": "Baking sourdough", "confidence": 0.99}]
    )

    await learn(mem.learning, said("It's Rowen, with an e."), llm, "fake-model")
    assert await mem.service.get_profile() == {}  # nothing active without approval
    assert await mem.service.list(pending=False) == []
    assert await mem.service.list(pending=True)  # both queued as candidates
    await mem.store.close()


async def test_sensitive_fact_is_never_learned(tmp_path: Path) -> None:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = _extraction_llm(facts=[{"content": "wifi password is hunter2", "confidence": 0.99}])

    await learn(mem.learning, said("hello"), llm, "fake-model")
    assert await mem.service.list(include_archived=True) == []  # dropped, active or pending
    await mem.store.close()


async def test_self_correction_supersedes_profile(tmp_path: Path) -> None:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.set_profile("name", "Rowan")
    llm = _extraction_llm(profile={"name": "Rowen"})

    await learn(mem.learning, said("Sorry, my name is Rowen."), llm, "fake-model")
    assert await mem.service.get_profile() == {"name": "Rowen"}  # superseded, not duplicated
    profiles = await mem.service.list(kind=MemoryKind.profile)
    assert profiles and profiles[0].source == "inferred"  # a learned correction is labeled inferred
    await mem.store.close()


async def test_address_directive_routes_to_slot_not_a_preference(tmp_path: Path) -> None:
    # "Don't call me by name" is a deterministic Tier-1 slot, never a stacked preference,
    # and it is still announced (no silent writes) so the user can see and undo it.
    bus = InProcessEventBus()
    learned: list[Event] = []

    async def collect(event: Event) -> None:
        learned.append(event)

    bus.subscribe("memory.learned", collect)
    mem = await build_fake_memory_runtime(tmp_path, bus)
    llm = _extraction_llm(
        facts=[{"content": "User asked not to be called by name", "confidence": 0.9}]
    )

    await learn(mem.learning, said("hello"), llm, "fake-model")

    assert (await mem.service.get_profile())["address_by_name"] == "never"  # the slot, set
    assert not await mem.service.list(kind=MemoryKind.preference)  # not a stacked preference
    assert learned and learned[-1].payload["summary"] == "Won't use your name"  # announced
    await mem.store.close()


async def test_address_correction_already_applied_does_not_re_announce(tmp_path: Path) -> None:
    # The in-call hook may already have set the slot mid-call; the session-end extractor
    # must not re-notify for the same unchanged correction (no UI spam).
    bus = InProcessEventBus()
    learned: list[Event] = []

    async def collect(event: Event) -> None:
        learned.append(event)

    bus.subscribe("memory.learned", collect)
    mem = await build_fake_memory_runtime(tmp_path, bus)
    await mem.service.set_address_mode(AddressByName.never)  # already applied (e.g. mid-call)

    llm = _extraction_llm(facts=[{"content": "don't call me by name", "confidence": 0.9}])
    await learn(mem.learning, said("hello"), llm, "fake-model")

    assert learned == []  # the slot was unchanged -> no redundant memory.learned
    assert (await mem.service.get_profile())["address_by_name"] == "never"
    await mem.store.close()


async def test_set_mode_is_live_and_broadcasts(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    modes: list[str] = []

    async def collect(event: Event) -> None:
        modes.append(event.payload["mode"])

    bus.subscribe("memory.learning_mode", collect)
    mem = await build_fake_memory_runtime(tmp_path, bus)
    assert mem.learning.mode is LearningMode.proactive  # default

    await mem.learning.set_mode(LearningMode.review_only)
    assert mem.learning.mode is LearningMode.review_only
    assert modes == ["review_only"]
    await mem.store.close()


async def test_approve_activates_candidate(tmp_path: Path) -> None:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = _extraction_llm(facts=[{"content": "Sourdough starter dough", "confidence": 0.2}])

    await learn(mem.learning, said("hello"), llm, "fake-model")
    candidate = (await mem.service.list(pending=True))[0]
    assert candidate.pending

    await mem.service.approve(candidate.id)
    ctx = await mem.retriever.retrieve("sourdough starter")  # now active + retrievable
    assert any("Sourdough" in item.memory.content for item in ctx.items)
    await mem.store.close()


async def test_episodic_is_not_auto_stored(tmp_path: Path) -> None:
    # Episodic "what happened this call" notes are transient (the session summary
    # already covers the call), so they are dropped rather than stored.
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = _extraction_llm(
        facts=[{"content": "User asked what to do next", "confidence": 0.95, "kind": "episodic"}]
    )
    await learn(mem.learning, said("hello"), llm, "fake-model")
    assert await mem.service.list(include_archived=True) == []  # not active, not queued
    await mem.store.close()


async def test_duplicate_fact_does_not_stack(tmp_path: Path) -> None:
    # The extractor re-proposing the same fact across sessions must not stack rows;
    # facts are deduped on normalized content.
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    fact = {"content": "User has a cat named Max", "confidence": 0.95}
    await learn(mem.learning, said("I have a cat"), _extraction_llm(facts=[fact]), "m")
    await learn(mem.learning, said("Max is my cat"), _extraction_llm(facts=[fact]), "m")
    matches = [m for m in await mem.service.list() if "cat named Max" in m.content]
    assert len(matches) == 1  # the second ingest deduped
    await mem.store.close()


async def test_store_cap_prunes_lru_keeps_profile_and_pinned(tmp_path: Path) -> None:
    # A hard ceiling LRU-prunes the oldest unpinned fact and never touches the Tier-1
    # profile or pinned memories.
    mem = await build_fake_memory_runtime(
        tmp_path, InProcessEventBus(), MemorySettings(max_active_memories=3)
    )
    await mem.service.set_profile("name", "Morgan")  # Tier-1 — never pruned
    pinned = await mem.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="a pinned fact", pinned=True)
    )
    for i in range(4):  # four prunable facts against a ceiling of three
        await mem.service.add(MemoryCreate(kind=MemoryKind.fact, content=f"fact number {i}"))

    active = await mem.service.list()
    prunable = [m for m in active if not m.pinned and m.kind is not MemoryKind.profile]
    assert len(prunable) == 3  # capped at the ceiling
    assert any(m.id == pinned.id for m in active)  # pinned survived
    assert (await mem.service.get_profile())["name"] == "Morgan"  # profile survived
    assert not any("number 0" in m.content for m in active)  # the oldest LRU was archived
    await mem.store.close()


async def test_a_name_the_user_never_gave_is_not_learned(tmp_path: Path) -> None:
    # Asked for a name it wasn't given, an extractor fills one in: a placeholder, or her
    # own name from her greeting. Neither may become the user's name.
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.set_profile("name", "Rowan")
    session = [
        {"role": "assistant", "content": "Hi, I'm Veronica. What can I help with?"},
        {"role": "user", "content": "Could you help me word an email?"},
    ]
    for invented in ("Veronica", "User", "Unknown", " "):
        await learn(mem.learning, session, _extraction_llm(profile={"name": invented}), "m")
    assert await mem.service.get_profile() == {"name": "Rowan"}
    await mem.store.close()


async def test_a_session_where_only_she_spoke_is_not_sent_to_the_extractor(
    tmp_path: Path,
) -> None:
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = _RecordingLLM()
    greeting = [{"role": "assistant", "content": "Hi, I'm Veronica."}]
    await learn(runtime.learning, greeting, llm, "m")  # type: ignore[arg-type]
    assert llm.keep_alive == "unset"  # never asked
    await runtime.store.close()


def test_the_transcript_leaves_out_what_tools_returned() -> None:
    # A file she read or a memory she recalled isn't something the user told her.
    history = [
        {"role": "user", "content": "Read my notes"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "files_read"}}]},
        {"role": "tool", "content": "Rowan is allergic to cats"},
        {"role": "assistant", "content": "Done."},
    ]
    assert session_transcript(history) == "User: Read my notes\nVeronica: Done."


def test_an_empty_profile_slot_keeps_the_rest_of_the_extraction() -> None:
    reply = '{"profile": {"name": null, "city": " Leeds "}, "facts": [{"content": "Likes tea"}]}'
    extraction = parse_extraction(reply)
    assert extraction.profile == {"city": "Leeds"}
    assert [fact.content for fact in extraction.facts] == ["Likes tea"]


def test_a_profile_value_is_kept_only_when_the_user_said_it() -> None:
    session = [
        {"role": "assistant", "content": "Nice to meet you. I'm Veronica."},
        {"role": "user", "content": "Call me Ro. I live in Leeds."},
    ]
    profile = {
        "nickname": "Ro",
        "name": "Veronica",
        "city": "Leeds, UK",  # "UK" is the extractor's, not the user's
        "address_by_name": "never",
    }
    assert kept_profile(profile, session) == {"nickname": "Ro", "address_by_name": "never"}


def test_a_name_is_kept_only_when_the_user_gave_it_as_theirs() -> None:
    sister = [{"role": "user", "content": "My sister Morgan lives in Leeds."}]
    assert kept_profile({"name": "Morgan (sister)"}, sister) == {}
    assert kept_profile({"name": "Morgan"}, sister) == {}
    corrected = [{"role": "user", "content": "My name's Robin, wait, sorry. It's Rowan."}]
    assert kept_profile({"name": "Rowan"}, corrected) == {"name": "Rowan"}
    introduced = [{"role": "user", "content": "Hi, I'm Sam Carter and I need a hand."}]
    assert kept_profile({"name": "Sam Carter"}, introduced) == {"name": "Sam Carter"}
    # Other slots only need to be in the user's words.
    assert kept_profile({"city": "Leeds"}, sister) == {"city": "Leeds"}
