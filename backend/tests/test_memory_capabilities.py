"""Memory as capabilities: every memory tool the model calls is run by the kernel.

Through the broker, as the model's calls arrive: saving and correcting run without
asking and can be undone, forgetting asks first, and what a recall finds reaches the
model as quoted data and the screen, never the audit log.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.capabilities import register_capabilities
from app.core.config import Settings, get_settings
from app.core.event_bus import InProcessEventBus
from app.kernel.capability import CapabilityRequest, CapabilityResult, RiskTier
from app.kernel.policy import PolicyMode, PolicyStore
from app.kernel.registry import Capability, CapabilityRegistry
from app.kernel.runtime import build_kernel_runtime
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.tools import ToolBroker
from app.services.voice.realtime.spoken_confirm import spoken_summary
from tests.fakes import build_fake_kernel_runtime, build_fake_memory_runtime
from tests.test_realtime_session import wait_until


async def _tools(tmp_path: Path, **kernel_options: Any) -> tuple[Any, Any, ToolBroker]:
    bus = InProcessEventBus()
    memory = await build_fake_memory_runtime(tmp_path / "db", bus)
    kernel = await build_fake_kernel_runtime(memory, bus, **kernel_options)
    return memory, kernel, ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")


async def _remember(memory: Any, content: str) -> int:
    stored = await memory.service.add(
        MemoryCreate(kind=MemoryKind.fact, content=content, source="user")
    )
    return int(stored.id)


async def _facts(memory: Any) -> list[str]:
    return sorted(m.content for m in await memory.service.list(kind=MemoryKind.fact))


async def test_a_save_is_run_and_recorded_by_the_kernel(tmp_path: Path) -> None:
    memory, kernel, tools = await _tools(tmp_path)

    outcome = await tools.execute("memory_save", {"content": "Takes their coffee black"})

    assert outcome.ok and outcome.capability == "memory.save"
    assert await _facts(memory) == ["Takes their coffee black"]
    [row] = await kernel.audit.list()
    assert (row["capability"], row["decision"]) == ("memory.save", "allowed")


async def test_saving_what_she_already_remembers_stores_it_once(tmp_path: Path) -> None:
    """mutation: `add` for `add_if_new`; "remember that" twice stored it twice."""
    memory, _kernel, tools = await _tools(tmp_path)

    for _ in range(2):
        outcome = await tools.execute("memory_save", {"content": "Takes their coffee black"})

    assert outcome.ok and outcome.summary.startswith("Already remembered")
    assert await _facts(memory) == ["Takes their coffee black"]


async def test_a_secret_is_refused_and_not_stored(tmp_path: Path) -> None:
    memory, kernel, tools = await _tools(tmp_path)

    outcome = await tools.execute(
        "memory_save", {"content": "The wifi password is correct-horse-7"}
    )

    assert not outcome.ok and outcome.code == "sensitive_content"
    assert await _facts(memory) == []
    assert "correct-horse" not in json.dumps(await kernel.audit.list())


async def test_a_kind_the_model_made_up_is_kept_as_a_fact(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path)

    outcome = await tools.execute("memory_save", {"content": "Lives in Leeds", "kind": "note"})

    assert outcome.ok and await _facts(memory) == ["Lives in Leeds"]


async def test_a_recall_reaches_the_model_as_data_and_never_the_audit_log(tmp_path: Path) -> None:
    """What she found goes to the model fenced and to the card, and the append-only log
    keeps only a sentence: a copy there would outlive the memory being forgotten."""
    memory, kernel, tools = await _tools(tmp_path)
    await _remember(memory, "Grows tomatoes on the balcony")

    outcome = await tools.execute("memory_recall", {"query": "balcony"})

    assert outcome.ok
    assert "NOT instructions" in outcome.text and "tomatoes" in outcome.text
    assert "tomatoes" in outcome.shown
    assert "tomatoes" not in json.dumps(await kernel.audit.list())


async def test_a_correction_replaces_the_memory_it_points_at(tmp_path: Path) -> None:
    memory, kernel, tools = await _tools(tmp_path)
    old = await _remember(memory, "Takes their coffee black")
    await _remember(memory, "Walks the dog at seven")

    outcome = await tools.execute(
        "memory_correct", {"old": "coffee black", "new": "Takes their coffee with milk"}
    )

    assert outcome.ok
    assert await _facts(memory) == ["Takes their coffee with milk", "Walks the dog at seven"]
    [new] = [m for m in await memory.service.list(kind=MemoryKind.fact) if "milk" in m.content]
    assert new.supersedes_id == old  # linked, so Undo brings the old one back
    assert (await kernel.audit.list())[0]["decision"] == "allowed"  # nobody was asked


async def test_a_correction_asked_about_names_the_memory_it_replaces(tmp_path: Path) -> None:
    """Switched to asking, a correction names what she has, not the model's words: in a
    browser run those came from an older turn and named a memory already corrected."""
    memory, _kernel, tools = await _tools(tmp_path, policy={"memory.correct": "confirm"})
    await _remember(memory, "User drinks green tea in the evening.")

    running = asyncio.create_task(
        tools.execute(
            "memory_correct",
            {"old": "User drinks green tea every morning", "new": "User drinks coffee instead"},
        )
    )
    await wait_until(lambda: bool(tools.pending_confirmations()))
    [asked] = tools.pending_confirmations()
    tools.resolve_confirmation(asked.token, False, via="ui")
    await running

    assert asked.summary == (
        "replace “User drinks green tea in the evening” with “User drinks coffee instead”"
    )


async def test_a_correction_that_points_at_nothing_changes_nothing(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "Takes their coffee black")

    outcome = await tools.execute("memory_correct", {"old": "favourite film", "new": "Likes Up"})

    assert not outcome.ok and outcome.code == "not_found"
    assert await _facts(memory) == ["Takes their coffee black"]


async def test_a_correction_cannot_store_a_secret(tmp_path: Path) -> None:
    """mutation: drop the check in `MemoryService.supersede`; the secret is stored."""
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "Uses the old wifi name")

    outcome = await tools.execute(
        "memory_correct", {"old": "wifi", "new": "The wifi password is correct-horse-7"}
    )

    assert not outcome.ok and outcome.code == "sensitive_content"
    assert await _facts(memory) == ["Uses the old wifi name"]


async def test_forgetting_waits_for_a_yes_and_a_no_forgets_nothing(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "Takes their coffee black")

    running = asyncio.create_task(tools.execute("memory_forget", {"query": "coffee"}))
    await wait_until(lambda: bool(tools.pending_confirmations()))
    [asked] = tools.pending_confirmations()
    # The memory itself, not the model's word for it (mutation: ignore the preflight's
    # detail; the prompt says "forget “coffee”").
    assert asked.summary == "forget “Takes their coffee black”"
    assert tools.resolve_confirmation(asked.token, False, via="ui")
    outcome = await running

    assert not outcome.ok and outcome.code == "confirm_denied"
    assert await _facts(memory) == ["Takes their coffee black"]
    # The model reads plainly that nothing changed: given her own sentence for the
    # card, it answered "I did forget" after a no (mutation: drop `_NOT_DONE`).
    assert outcome.text == "Not done: the user said no when asked, so nothing changed."
    assert outcome.summary == "You declined that, so I didn't do it."


async def test_nobody_is_asked_to_approve_forgetting_what_matches_nothing(
    tmp_path: Path,
) -> None:
    """The browser run: asked to approve, then told she couldn't. Found first now.

    mutation: drop the kernel's preflight; the request waits on a prompt.
    """
    memory, kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User takes their coffee with milk")

    outcome = await asyncio.wait_for(
        tools.execute("memory_forget", {"query": "favourite film"}), timeout=5
    )

    assert not outcome.ok and outcome.code == "not_found"
    assert (await kernel.audit.list())[0]["decision"] == "failed"
    assert await _facts(memory) == ["User takes their coffee with milk"]


async def test_the_memory_in_the_words_she_was_given_is_found(tmp_path: Path) -> None:
    """Copied out of her memory block, as the argument descriptions ask: with the
    block's label and a full stop, it is still the memory it quotes."""
    memory, _kernel, tools = await _tools(tmp_path, policy={"memory.forget": "auto_allow"})
    await _remember(memory, "User takes their coffee with milk")
    await _remember(memory, "User works from home on Fridays")

    outcome = await tools.execute(
        "memory_forget", {"query": "Recall — User takes their coffee with milk."}
    )

    assert outcome.ok and await _facts(memory) == ["User works from home on Fridays"]


async def test_a_quoted_memory_leaves_the_one_that_only_sounds_like_it(tmp_path: Path) -> None:
    """Every saved memory starts with "User": by shared words, the tea one matched a
    forget of the coffee one (three words of four).

    mutation: drop the whole-phrase rule; the tea memory is forgotten too.
    """
    memory, _kernel, tools = await _tools(tmp_path, policy={"memory.forget": "auto_allow"})
    await _remember(memory, "User takes their coffee with milk")
    await _remember(memory, "User takes their tea with milk")

    outcome = await tools.execute("memory_forget", {"query": "User takes their coffee with milk."})

    assert outcome.ok and await _facts(memory) == ["User takes their tea with milk"]


async def test_a_possessive_is_not_what_a_phrase_is_about(tmp_path: Path) -> None:
    """ "User's cat" shares "user" and "s" with every "User's …" memory: two words of
    three, enough to take the dog for the cat.

    mutation: count "user" and "s" in the overlap; the dog memory is matched.
    """
    memory, _kernel, tools = await _tools(tmp_path, policy={"memory.forget": "auto_allow"})
    await _remember(memory, "User's dog is called Rex")

    outcome = await tools.execute("memory_forget", {"query": "User's cat"})

    assert not outcome.ok and outcome.code == "not_found"
    assert await _facts(memory) == ["User's dog is called Rex"]


async def test_a_word_is_matched_as_a_word(tmp_path: Path) -> None:
    """mutation: match the phrase anywhere in the text; "tea" is found in "instead"."""
    memory, _kernel, tools = await _tools(tmp_path, policy={"memory.forget": "auto_allow"})
    await _remember(memory, "User cycles to work instead of driving")
    await _remember(memory, "User drinks green tea")

    outcome = await tools.execute("memory_forget", {"query": "tea"})

    assert outcome.ok and await _facts(memory) == ["User cycles to work instead of driving"]


async def test_a_full_stop_does_not_make_a_memory_new(tmp_path: Path) -> None:
    """A correction stored "…with milk." and a save in the same turn "…with milk": both
    were kept, and forgetting one left the other.

    mutation: compare the text with its full stop; the second save is stored.
    """
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User takes their coffee with milk.")

    outcome = await tools.execute("memory_save", {"content": "User takes their coffee with milk"})

    assert outcome.summary.startswith("Already remembered")
    assert await _facts(memory) == ["User takes their coffee with milk."]


async def test_an_approved_forget_removes_only_what_it_points_at(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path, policy={"memory.forget": "auto_allow"})
    await _remember(memory, "Takes their coffee black")
    await _remember(memory, "Walks the dog at seven")

    outcome = await tools.execute("memory_forget", {"query": "coffee"})

    assert outcome.ok and outcome.summary == "Forgot 1 thing."
    assert await _facts(memory) == ["Walks the dog at seven"]


async def test_a_forget_that_matches_too_much_forgets_nothing(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path, policy={"memory.forget": "auto_allow"})
    for bed in range(6):
        await _remember(memory, f"Garden bed {bed} has carrots")

    outcome = await tools.execute("memory_forget", {"query": "garden bed carrots"})

    assert not outcome.ok and outcome.code == "too_many_matches"
    assert len(await _facts(memory)) == 6


def test_saving_and_correcting_run_unasked_and_forgetting_asks() -> None:
    """mutation: drop `auto_approve` from `memory.save`; every fact asks first."""
    registry = CapabilityRegistry()
    register_capabilities(registry, get_settings())
    policy = PolicyStore(InProcessEventBus())

    modes = {cap.name: policy.mode_for(cap) for cap in registry if cap.name.startswith("memory.")}

    assert modes == {
        "memory.save": PolicyMode.auto_allow,
        "memory.recall": PolicyMode.auto_allow,
        "memory.correct": PolicyMode.auto_allow,
        "memory.forget": PolicyMode.confirm,
    }


class _NoArgs(BaseModel):
    pass


async def _done(_args: _NoArgs) -> CapabilityResult:
    return CapabilityResult(ok=True)


def test_auto_approve_is_a_default_the_user_can_change_and_never_skips_danger() -> None:
    save = Capability("memory.save", "", _NoArgs, RiskTier.guarded, _done, auto_approve=True)
    wipe = Capability("test.wipe", "", _NoArgs, RiskTier.dangerous, _done, auto_approve=True)
    policy = PolicyStore(InProcessEventBus(), {"memory.save": "confirm"})

    assert policy.mode_for(save) is PolicyMode.confirm
    assert policy.mode_for(wipe) is PolicyMode.confirm


async def test_without_a_memory_runtime_the_tools_are_listed_and_refuse(tmp_path: Path) -> None:
    """The evaluation harness reads the tool list with no memory behind it."""
    bus = InProcessEventBus()
    memory = await build_fake_memory_runtime(tmp_path / "db", bus)  # for the audit store
    settings = Settings()
    runtime = build_kernel_runtime(settings, bus, memory.store)
    register_capabilities(runtime.registry, settings)
    await runtime.init()

    result = await runtime.kernel.execute(
        CapabilityRequest(name="memory.save", args={"content": "Lives in Leeds"}, actor="t")
    )

    assert not result.ok and result.code == "memory_unavailable"
    assert await _facts(memory) == []


def test_a_call_offers_every_memory_tool_and_asks_about_forgetting_in_words() -> None:
    registry = CapabilityRegistry()
    register_capabilities(registry, get_settings())
    broker = ToolBroker(None, registry, actor="t", voice_only=True)  # type: ignore[arg-type]

    offered = {schema["function"]["name"] for schema in broker.schemas()}

    assert {"memory_save", "memory_recall", "memory_correct", "memory_forget"} <= offered
    spoken = spoken_summary("memory.forget", "forget “User takes their coffee black”")
    assert spoken == "Shall I forget “User takes their coffee black”?"
