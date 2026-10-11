"""Memory wired into the live call: assembled context, welcome-back, session summary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.event_bus import InProcessEventBus
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.voice.turn import TurnEvent
from tests.fakes import (
    FakeDetector,
    FakeLLM,
    FakeSTT,
    FakeTTS,
    FakeWS,
    ListMetricsSink,
    build_fake_memory_runtime,
)
from tests.test_realtime_session import feed, make_session, start_call, states, wait_until

_UTTERANCE = [[TurnEvent.speech_start], [], [TurnEvent.speech_end]]


def _system_has(seen: list[Any], needle: str) -> bool:
    return any(
        msgs and msgs[0]["role"] == "system" and needle in msgs[0]["content"] for msgs in seen
    )


async def test_assembler_builds_tiered_block(tmp_path: Path) -> None:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.set_profile("name", "Rowan")
    await mem.service.add(MemoryCreate(kind=MemoryKind.fact, content="Rowan is baking sourdough"))
    block = await mem.assembler.assemble("what am I baking with the sourdough?")
    assert block is not None
    assert "Rowan" in block and "sourdough" in block  # Tier-1 profile + gated memory
    await mem.store.close()


async def test_memory_context_injected_into_reply(tmp_path: Path) -> None:
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Sourdough starter dough project")
    )
    seen: list[Any] = []
    session = make_session(
        ws,
        detector=FakeDetector(list(_UTTERANCE)),
        stt=FakeSTT([], "the sourdough starter"),
        tts=FakeTTS(),
        llm=FakeLLM(["Got it."], seen=seen),
        sink=ListMetricsSink(),
        memory=mem,
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: _system_has(seen, "Sourdough"))  # the memory reached the prompt
    await session._stop_call()
    await mem.store.close()


async def test_welcome_back_greets_known_user(tmp_path: Path) -> None:
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.set_profile("name", "Rowan")
    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
        memory=mem,
    )
    await start_call(session, ws)
    greetings = [m["content"] for m in session.history if m["role"] == "assistant"]
    assert any("Rowan" in greeting for greeting in greetings)  # varied, but always by name
    assert ws.sent_bytes  # the greeting was synthesized + streamed
    await session._stop_call()
    await mem.store.close()


async def test_session_summary_written_on_call_end(tmp_path: Path) -> None:
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    session = make_session(
        ws,
        detector=FakeDetector(list(_UTTERANCE)),
        stt=FakeSTT([], "hello veronica"),
        tts=FakeTTS(),
        llm=FakeLLM(["Hi there."]),
        sink=ListMetricsSink(),
        memory=mem,
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: states(ws)[-1] == "listening" and len(states(ws)) >= 5)
    await session._stop_call()

    for _ in range(2000):  # the summary runs in a background task after call end
        if await mem.service.list(kind=MemoryKind.summary):
            break
        await asyncio.sleep(0.001)
    assert await mem.service.list(kind=MemoryKind.summary)
    await mem.store.close()


async def test_memory_written_midcall_used_next_turn(tmp_path: Path) -> None:
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    seen: list[Any] = []
    script = [
        [TurnEvent.speech_start],
        [],
        [TurnEvent.speech_end],
        [TurnEvent.speech_start],
        [],
        [TurnEvent.speech_end],
    ]
    session = make_session(
        ws,
        detector=FakeDetector(script),
        stt=FakeSTT([], "tell me about the sourdough"),
        tts=FakeTTS(),
        llm=FakeLLM(["Sure."], seen=seen),
        sink=ListMetricsSink(),
        memory=mem,
    )
    await start_call(session, ws)
    await feed(session, 3)  # turn 1 — nothing in memory yet
    await wait_until(lambda: states(ws)[-1] == "listening" and len(states(ws)) >= 5)
    await mem.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Sourdough starter dough project")
    )
    await feed(session, 3)  # turn 2 — the freshly written memory should be injected
    await wait_until(lambda: _system_has(seen, "Sourdough"))
    await session._stop_call()
    await mem.store.close()


async def test_inline_stop_using_my_name_sets_slot(tmp_path: Path) -> None:
    # A spoken form-of-address correction is routed to the Tier-1 slot by a
    # deterministic in-call hook, so the prompt for this very turn already obeys it.
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    seen: list[Any] = []
    session = make_session(
        ws,
        detector=FakeDetector(list(_UTTERANCE)),
        stt=FakeSTT([], "please stop using my name"),
        tts=FakeTTS(),
        llm=FakeLLM(["Of course."], seen=seen),
        sink=ListMetricsSink(),
        memory=mem,
    )
    await start_call(session, ws)
    await feed(session, 3)
    # wait for the correction to reach a prompt (the respond turn, past LLM prewarm) — the
    # inline hook runs before that, so by here the slot is set.
    await wait_until(
        lambda: any("stop using my name" in (m.get("content") or "") for msgs in seen for m in msgs)
    )
    assert (await mem.service.get_profile()).get("address_by_name") == "never"
    await session._stop_call()
    await mem.store.close()


async def test_prefetch_failure_does_not_kill_reply() -> None:
    ws = FakeWS()

    async def boom(_text: str) -> str | None:
        raise RuntimeError("memory down")

    session = make_session(
        ws,
        detector=FakeDetector(list(_UTTERANCE)),
        stt=FakeSTT([], "hello"),
        tts=FakeTTS(),
        llm=FakeLLM(["Still here."]),
        sink=ListMetricsSink(),
        prefetch=boom,
    )
    await start_call(session, ws)
    await feed(session, 3)
    # the reply still completes (speaking -> listening) despite the prefetch raising
    await wait_until(lambda: "speaking" in states(ws) and states(ws)[-1:] == ["listening"])
    await session._stop_call()
