"""Which memory a correction or a forget changes, and what is settled before anyone is asked.

A correction runs unasked, so it must replace the memory it names and no other; a
forget asks, so a yes must change exactly what the prompt showed. A request that could
only end in "couldn't" is refused before a prompt shows its words.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.config_memory import MemorySettings
from app.core.event_bus import InProcessEventBus
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.tools import ToolBroker
from tests.fakes import build_fake_kernel_runtime, build_fake_memory_runtime
from tests.test_realtime_session import wait_until

CONFIRM_ALL = {"memory.save": "confirm", "memory.correct": "confirm"}


async def _tools(
    tmp_path: Path, memory_settings: MemorySettings | None = None, **options: Any
) -> tuple[Any, Any, ToolBroker]:
    bus = InProcessEventBus()
    memory = await build_fake_memory_runtime(tmp_path / "db", bus, memory_settings)
    kernel = await build_fake_kernel_runtime(memory, bus, **options)
    return memory, kernel, ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")


async def _remember(memory: Any, content: str, **fields: Any) -> int:
    create = MemoryCreate(kind=MemoryKind.fact, content=content, source="user", **fields)
    return int((await memory.service.add(create)).id)


async def _facts(memory: Any) -> list[str]:
    listed = await memory.service.list(kind=MemoryKind.fact, limit=-1)
    return sorted(m.content for m in listed)


async def test_a_correction_replaces_the_memory_it_quotes_not_the_newest_holding_it(
    tmp_path: Path,
) -> None:
    """Whole-phrase hits come back newest first, and the correction took the first
    (mutation: take the first hit)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User drives a blue van.")
    await _remember(memory, "User drives a blue van to work on Mondays.")

    outcome = await tools.execute(
        "memory_correct", {"old": "User drives a blue van.", "new": "User drives a red van."}
    )

    assert outcome.ok
    assert await _facts(memory) == [
        "User drives a blue van to work on Mondays.",
        "User drives a red van.",
    ]


async def test_an_unasked_correction_needs_every_word_of_the_memory(tmp_path: Path) -> None:
    """ "Tea with milk" shared two words of three with the coffee memory and replaced
    it, with nobody asked (mutation: the loose share for an unasked correction)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User takes their coffee with milk.")

    outcome = await tools.execute(
        "memory_correct", {"old": "User takes tea with milk", "new": "User takes tea black."}
    )

    assert outcome.code == "not_found"
    assert await _facts(memory) == ["User takes their coffee with milk."]


async def test_a_phrase_with_no_words_of_its_own_points_at_nothing(tmp_path: Path) -> None:
    """ "User" is in every memory as a whole phrase, and replaced the newest (mutation:
    check for words after the whole-phrase search)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User grows tomatoes.")

    for old in ("User", "User's"):
        outcome = await tools.execute("memory_correct", {"old": old, "new": "User grows beans."})
        assert outcome.code == "not_found"
    assert await _facts(memory) == ["User grows tomatoes."]


async def test_a_correction_that_fits_two_memories_asks_which(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User swims on Tuesdays at the lido.")
    await _remember(memory, "User swims on Tuesdays with Morgan.")

    outcome = await tools.execute(
        "memory_correct", {"old": "User swims on Tuesdays", "new": "User swims on Fridays."}
    )

    assert outcome.code == "too_many_matches" and "more than one" in outcome.summary
    assert len(await _facts(memory)) == 2


async def test_a_forget_changes_what_the_prompt_named_and_nothing_saved_since(
    tmp_path: Path,
) -> None:
    """Looked up again after the yes, a forget took a memory saved while the prompt was
    up, which nobody was asked about (mutation: ignore what the preflight named)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User collects stamps.")

    running = asyncio.create_task(tools.execute("memory_forget", {"query": "User collects stamps"}))
    await wait_until(lambda: bool(tools.pending_confirmations()))
    await _remember(memory, "User collects stamps from Japan.")
    [asked] = tools.pending_confirmations()
    tools.resolve_confirmation(asked.token, True, via="ui")
    outcome = await running

    assert outcome.ok and outcome.summary == "Forgot 1 thing."
    assert await _facts(memory) == ["User collects stamps from Japan."]


async def test_the_model_cannot_send_what_the_prompt_named(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path)
    kept = await _remember(memory, "User collects stamps.")

    outcome = await tools.execute("memory_forget", {"query": "anything", "_named": [kept]})

    assert outcome.code == "invalid_args" and await _facts(memory) == ["User collects stamps."]


async def test_a_corrected_memory_keeps_what_made_it_matter(tmp_path: Path) -> None:
    """A pinned allergy came back unpinned once corrected, open to pruning (mutation:
    create the new memory without the old one's pin)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User is allergic to peanuts.", pinned=True, importance=0.9)

    await tools.execute(
        "memory_correct",
        {"old": "User is allergic to peanuts.", "new": "User is allergic to peanuts and cashews."},
    )

    [new] = await memory.service.list(kind=MemoryKind.fact)
    assert new.content.endswith("cashews.") and new.pinned and new.importance == 0.9


async def test_a_recall_lists_what_she_knows_when_a_name_is_set(tmp_path: Path) -> None:
    """With a name in the profile the block was never empty, so the list of what she
    knows never ran and a recall answered with the name alone (mutation: fall back only
    when the block is empty)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await memory.service.set_profile("name", "Rowan")
    await _remember(memory, "User grows tomatoes on the balcony.")

    outcome = await tools.execute("memory_recall", {"query": "what do you remember about me"})

    assert "Rowan" in outcome.shown and "tomatoes" in outcome.shown


async def test_a_secret_is_refused_before_a_prompt_could_show_it(tmp_path: Path) -> None:
    """Asked about first, a secret was on the card and said aloud on a call, and then
    refused anyway (mutation: drop the secret check from the preflights)."""
    memory, kernel, tools = await _tools(tmp_path, policy=CONFIRM_ALL)
    await _remember(memory, "User's router is in the hall.")
    secret = "The wifi password is correct-horse-7"

    saved = await tools.execute("memory_save", {"content": secret})
    corrected = await tools.execute(
        "memory_correct", {"old": "User's router is in the hall.", "new": secret}
    )

    assert saved.code == corrected.code == "sensitive_content"
    assert not tools.pending_confirmations()
    assert all(row["decision"] == "failed" for row in await kernel.audit.list())


async def test_what_she_already_remembers_is_not_asked_about_again(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path, policy=CONFIRM_ALL)
    await _remember(memory, "User takes their coffee black.")

    outcome = await tools.execute("memory_save", {"content": "User takes their coffee black"})
    blank = await tools.execute("memory_save", {"content": "   "})

    assert outcome.code == "already_exists" and outcome.summary == "I already remember that."
    assert blank.code == "invalid_args" and not tools.pending_confirmations()


async def test_a_memory_too_long_to_ever_be_shown_is_not_kept(tmp_path: Path) -> None:
    """Past 2,048 characters a memory no longer fits the block and never reaches a
    prompt (mutation: drop `max_length`)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await _remember(memory, "User grows tomatoes.")
    long = "User " + "really " * 400 + "likes tea."

    saved = await tools.execute("memory_save", {"content": long})
    corrected = await tools.execute("memory_correct", {"old": "User grows tomatoes.", "new": long})

    assert saved.code == corrected.code == "invalid_args"
    assert await _facts(memory) == ["User grows tomatoes."]


async def test_a_memory_past_the_listings_first_page_can_be_corrected(tmp_path: Path) -> None:
    """The lookup read the newest 200 of each kind, so an older memory she could still
    recall was "nothing I remember" (mutation: the listing's default limit)."""
    memory, _kernel, tools = await _tools(tmp_path, MemorySettings(max_active_memories=400))
    await _remember(memory, "User was born in a lighthouse.")
    for n in range(200):
        await _remember(memory, f"User has note number {n}.")

    outcome = await tools.execute(
        "memory_correct",
        {"old": "User was born in a lighthouse.", "new": "User was born in a windmill."},
    )

    assert outcome.ok
