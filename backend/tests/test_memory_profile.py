"""Her profile ("About you") corrected or forgotten in conversation (bug 38): it was slots
of its own that no correction or forget could reach, and the Memory page was the only
way ("That's part of your profile, which I can't change from here")."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.memory_profile import slot_value
from app.services.memory.address import ADDRESS_KEY
from app.services.memory.models import Memory, MemoryKind
from tests.test_memory_targets import CONFIRM_ALL, _tools
from tests.test_realtime_session import wait_until


def _slot(key: str, value: str) -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id=1,
        kind=MemoryKind.profile,
        key=key,
        content=value,
        normalized_content=value.lower(),
        importance=1.0,
        confidence=1.0,
        pinned=True,
        source="user",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    ("key", "value", "old", "new", "found"),
    [
        # Quoted from her prompt, and written as she saves a memory.
        ("name", "Rowan", "The user's name is Rowan", "User's name is Morgan.", "Morgan"),
        ("name", "Rowan", "name: Rowan", "name: Morgan", "Morgan"),  # the profile's own form
        ("location", "Leeds", "User lives in Leeds", "User lives in York", "York"),
        ("occupation", "nurse", "occupation: nurse", "Occupation: senior nurse.", "senior nurse"),
        ("name", "Rowan", "Rowan", "Morgan", "Morgan"),
        # Not for certain: said another way round, the same, or under another slot's name.
        ("location", "Leeds", "User lives in Leeds", "User has moved to York", None),
        ("name", "Rowan", "The user's name is Rowan", "The user's name is Rowan", None),
        ("name", "Rowan", "name: Rowan", "nickname: Mo", None),
    ],
)
def test_what_a_correction_sets_a_slot_to(
    key: str, value: str, old: str, new: str, found: str | None
) -> None:
    """A name is not to be guessed (mutations: take any word that's new; any key)."""
    assert slot_value(_slot(key, value), old, new) == found


async def test_a_correction_of_her_profile_changes_it_with_undo(tmp_path: Path) -> None:
    """(mutation: refuse it as the Memory page's to change)"""
    memory, _kernel, tools = await _tools(tmp_path)
    await memory.service.set_profile("name", "Rowan")

    outcome = await tools.execute(
        "memory_correct", {"old": "The user's name is Rowan", "new": "User's name is Morgan."}
    )

    assert outcome.ok and outcome.summary == "Changed your name to Morgan."
    assert await memory.service.get_profile() == {"name": "Morgan"}
    [changed] = await memory.service.list(kind=MemoryKind.profile, limit=-1)
    await memory.service.undo(changed.id)  # the notification's Undo
    assert await memory.service.get_profile() == {"name": "Rowan"}
    await memory.store.close()


async def test_a_new_value_that_cant_be_told_asks_for_the_form(tmp_path: Path) -> None:
    """Refused, it says the form that works, so the model's next try can (mutation:
    guess from the words that are new)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await memory.service.set_profile("location", "Leeds")

    outcome = await tools.execute(
        "memory_correct", {"old": "User lives in Leeds", "new": "User has moved to York"}
    )
    retried = await tools.execute(
        "memory_correct", {"old": "location: Leeds", "new": "location: York"}
    )

    assert not outcome.ok and "“location: …”" in outcome.summary
    assert retried.ok and await memory.service.get_profile() == {"location": "York"}
    await memory.store.close()


async def test_a_correction_asked_about_names_the_change(tmp_path: Path) -> None:
    memory, _kernel, tools = await _tools(tmp_path, policy=CONFIRM_ALL)
    await memory.service.set_profile("name", "Rowan")

    running = asyncio.create_task(
        tools.execute("memory_correct", {"old": "name: Rowan", "new": "name: Morgan"})
    )
    await wait_until(lambda: bool(tools.pending_confirmations()))
    [asked] = tools.pending_confirmations()
    tools.resolve_confirmation(asked.token, True, via="ui")
    outcome = await running

    assert asked.summary == "change your name from “Rowan” to “Morgan”"
    assert outcome.ok and await memory.service.get_profile() == {"name": "Morgan"}
    await memory.store.close()


async def test_a_forget_of_her_profile_asks_and_forgets_the_slot(tmp_path: Path) -> None:
    """(mutation: refuse it as the Memory page's to change)"""
    memory, _kernel, tools = await _tools(tmp_path)
    await memory.service.set_profile("occupation", "nurse")

    running = asyncio.create_task(tools.execute("memory_forget", {"query": "User is a nurse"}))
    await wait_until(lambda: bool(tools.pending_confirmations()))
    [asked] = tools.pending_confirmations()
    tools.resolve_confirmation(asked.token, True, via="ui")
    outcome = await running

    assert asked.summary == "forget your occupation (“nurse”)"
    assert outcome.ok and await memory.service.get_profile() == {}
    await memory.store.close()


async def test_of_two_slots_with_one_value_the_one_named_changes(tmp_path: Path) -> None:
    """(mutation: take the first slot holding the value)"""
    memory, _kernel, tools = await _tools(tmp_path)
    await memory.service.set_profile("nickname", "Rowan")
    await memory.service.set_profile("name", "Rowan")

    unnamed = await tools.execute("memory_correct", {"old": "Rowan", "new": "Mo"})
    named = await tools.execute(
        "memory_correct", {"old": "The user's name is Rowan", "new": "User's name is Morgan."}
    )

    assert not unnamed.ok and unnamed.code == "too_many_matches"  # which one isn't said
    assert named.ok
    assert await memory.service.get_profile() == {"nickname": "Rowan", "name": "Morgan"}
    await memory.store.close()


async def test_a_profile_correction_needs_no_memory_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot is never embedded; superseded like a fact, it was (mutation: embed it)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await memory.service.set_profile("name", "Rowan")

    async def unavailable(_item: Any) -> None:
        raise RuntimeError("the memory model isn't installed")

    monkeypatch.setattr(memory.service, "_vector_for", unavailable)

    outcome = await tools.execute("memory_correct", {"old": "name: Rowan", "new": "name: Morgan"})

    assert outcome.ok and await memory.service.get_profile() == {"name": "Morgan"}
    await memory.store.close()


async def test_how_she_addresses_them_is_not_a_slot_a_correction_reaches(
    tmp_path: Path,
) -> None:
    """Its values are an enum the save sets (mutation: count every slot)."""
    memory, _kernel, tools = await _tools(tmp_path)
    await memory.service.set_profile(ADDRESS_KEY, "never")

    outcome = await tools.execute("memory_correct", {"old": "never", "new": "often"})

    assert not outcome.ok and outcome.code == "not_found"
    assert await memory.service.get_profile() == {ADDRESS_KEY: "never"}
    await memory.store.close()
