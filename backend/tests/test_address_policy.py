"""Form-of-address as a deterministic Tier-1 slot: classifier, directive, routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.event_bus import InProcessEventBus
from app.services.memory.address import (
    ADDRESS_KEY,
    address_directive,
    address_summary,
    classify_directive,
    coerce_address,
    display_name,
)
from app.services.memory.assembler import render_block
from app.services.memory.models import AddressByName, MemoryKind, RetrievedContext
from app.services.tools import ToolBroker
from tests.fakes import build_fake_kernel_runtime, build_fake_memory_runtime


def test_coerce_defaults_unknown_to_sparingly() -> None:
    assert coerce_address("never") is AddressByName.never
    assert coerce_address("often") is AddressByName.often
    assert coerce_address(None) is AddressByName.sparingly  # absent slot -> the default
    assert coerce_address("garbage") is AddressByName.sparingly  # never trust raw content


@pytest.mark.parametrize(
    "text",
    [
        "don't call me by name",
        "stop using my name",
        "please don't say my name",
        "the user doesn't want to be called by name",
        "User asked not to be called by name",
    ],
)
def test_classify_recognizes_stop_using_my_name(text: str) -> None:
    assert classify_directive(text) is AddressByName.never


@pytest.mark.parametrize(
    "text",
    [
        "use my name more",
        "call me by my name",
        "please use my name",
        "the user prefers to be called by their name",
    ],
)
def test_classify_recognizes_use_my_name(text: str) -> None:
    assert classify_directive(text) is AddressByName.often


@pytest.mark.parametrize(
    "text",
    [
        "I like tea",
        "don't change my name",  # a name change, not a form-of-address rule
        "what's my name?",  # a recall, not an instruction
        "call me at five",  # no "name" -> not about addressing
        "my name is Morgan",  # setting the name, handled by profile extraction
    ],
)
def test_classify_ignores_unrelated_text(text: str) -> None:
    assert classify_directive(text) is None


def test_directive_never_suppresses_the_name() -> None:
    out = address_directive({"name": "Morgan", ADDRESS_KEY: "never"})
    assert out == "Do not address the user by name."
    assert out is not None and "Morgan" not in out  # the model cannot echo what it isn't given


def test_directive_sparingly_caps_frequency() -> None:
    out = address_directive({"name": "Morgan"})  # default slot
    assert out is not None and "Morgan" in out and "at most once" in out


def test_directive_often_states_the_name() -> None:
    out = address_directive({"name": "Morgan", ADDRESS_KEY: "often"})
    assert out is not None and "Morgan" in out and "every reply" in out


def test_directive_prefers_nickname_and_needs_a_name() -> None:
    assert display_name({"nickname": "Mo", "name": "Morgan"}) == "Mo"
    assert address_directive({ADDRESS_KEY: "sparingly"}) is None  # nothing to say without a name


def test_summary_is_human_and_calm() -> None:
    assert address_summary(AddressByName.never) == "Won't use your name"
    assert address_summary(AddressByName.often) == "Will use your name more often"


def test_render_block_emits_directive_not_a_recitable_name() -> None:
    block = render_block(
        RetrievedContext(
            profile={"name": "Morgan", ADDRESS_KEY: "never", "pronouns": "he/him"}, items=[]
        )
    )
    assert block is not None
    assert "Do not address the user by name." in block
    assert "Morgan" not in block  # never dumped as an echo-able fact
    assert ADDRESS_KEY not in block  # the control slot itself never leaks into the prompt
    assert "Profile — pronouns: he/him." in block  # other profile keys still render


async def test_set_address_mode_is_one_slot(tmp_path: Path) -> None:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.set_address_mode(AddressByName.sparingly)
    await mem.service.set_address_mode(AddressByName.never)  # reconciles in place, no stacking
    assert (await mem.service.get_profile())[ADDRESS_KEY] == "never"
    slots = [m for m in await mem.service.list(kind=MemoryKind.profile) if m.key == ADDRESS_KEY]
    assert len(slots) == 1
    await mem.store.close()


async def test_assembler_honors_never_over_the_live_store(tmp_path: Path) -> None:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.set_profile("name", "Morgan")
    await mem.service.set_address_mode(AddressByName.never)
    block = await mem.assembler.assemble("hello there")
    assert block is not None and "Do not address the user by name." in block
    assert "Morgan" not in block
    await mem.store.close()


async def test_save_tool_routes_to_slot_not_a_preference(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    mem = await build_fake_memory_runtime(tmp_path, bus)
    kernel = await build_fake_kernel_runtime(mem, bus)
    await mem.service.set_profile("name", "Morgan")
    tools = ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")

    reply = await tools.execute("memory_save", {"content": "Don't call me by name"})

    assert "won't use your name" in reply.text.lower()  # a calm ack, not "Saved: ..."
    profile = await mem.service.get_profile()
    assert profile["name"] == "Morgan" and profile[ADDRESS_KEY] == "never"  # both slots reconcile
    assert not await mem.service.list(kind=MemoryKind.preference)  # never stacked as a preference
    await mem.store.close()
