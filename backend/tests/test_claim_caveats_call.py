"""The claim check on a call: what her reply says she did, against what she did.

The caveat is shown apart from the reply and said after it, and kept after the reply in
history once shown (bug 37): talked over, it stays with a reply heard whole and goes with
one cut short. Nothing about it may change what history keeps of the reply itself:
talking over it, hanging up during it, or it failing to be said.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.schemas.events import Event
from app.services.memory.address import ADDRESS_KEY
from app.services.tools.claims import CAVEAT
from app.services.voice.realtime import spoken_confirm, workers
from app.services.voice.realtime.voice_events import VoiceEvent
from tests.test_voice_confirm import _session, _spoken, _until_asking, _write_call

MEMORY = CAVEAT["memory"]
FILES = CAVEAT["files"]


def _read_call(path: Path) -> dict[str, Any]:
    return {"tool_calls": [{"function": {"name": "files_read", "arguments": {"path": str(path)}}}]}


class _Voice:
    """Makes her reply's audio at once; holds the caveat mid-synthesis, or fails it."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.holding = asyncio.Event()
        self.release = asyncio.Event()

    async def activate_voice(self, _voice_id: str, *, load: bool = True) -> None:
        return None

    def synthesize_stream(self, text: str, _voice_id: str) -> Any:
        async def gen() -> Any:
            if self.fail and text in CAVEAT.values():
                raise RuntimeError("synthesis failed")
            yield text.encode()
            if text in CAVEAT.values():
                self.holding.set()
                await self.release.wait()

        return gen()


def _replies(session: Any) -> list[str]:
    return [m["content"] for m in session.history if m["role"] == "assistant"]


async def test_her_spoken_stop_using_my_name_backs_her_saying_so(tmp_path: Path) -> None:
    """The call writes that slot itself, with no tool, before the prompt (mutation: don't
    count it)."""
    memory, _kernel, session = await _session(
        tmp_path, [{"tokens": ["Got it, I'll remember not to use your name."]}]
    )

    await workers.respond(session, "Please stop using my name.")

    assert (await memory.service.get_profile()).get(ADDRESS_KEY) == "never"
    assert MEMORY not in _spoken(session)
    await memory.store.close()


async def test_her_name_change_backs_update_wording_too(tmp_path: Path) -> None:
    """The slot was changed, so "I've updated your preferences" is true (mutation: back
    only a save with it)."""
    memory, _kernel, session = await _session(
        tmp_path, [{"tokens": ["Got it, I've updated your preferences."]}]
    )

    await workers.respond(session, "Please stop using my name.")

    spoken = _spoken(session)  # read once: reading takes the audio
    assert not any(text in spoken for text in CAVEAT.values())
    await memory.store.close()


async def test_an_action_the_user_turned_down_gets_no_caveat(tmp_path: Path) -> None:
    """ "I'll write that to notes.md", then no: a caveat right after the no was noise."""
    target = tmp_path / "notes.md"
    step = _write_call(target)
    step["tokens"] = ["I'll write that to notes.md."]
    memory, _kernel, session = await _session(tmp_path, [step, {"tokens": ["Okay, I won't."]}])

    turn = asyncio.create_task(workers.respond(session, "write hello to notes.md"))
    await _until_asking(session)
    assert await spoken_confirm.answer(session, "no")
    await asyncio.wait_for(turn, 5)

    assert not target.exists() and FILES not in _spoken(session)
    await memory.store.close()


async def test_the_reply_is_in_history_before_the_caveat_is_said(tmp_path: Path) -> None:
    """Hanging up copies the history; talking over the caveat trims the reply. Both
    must find the reply already there, whole, with the caveat shown after it, and still
    marked as repeating what a tool returned (mutation: record it after the caveat)."""
    source = tmp_path / "plan.md"
    source.write_text("Tea at four.", encoding="utf-8")
    reply = "I've saved that you like tea at four."
    memory, _kernel, session = await _session(tmp_path, [_read_call(source), {"tokens": [reply]}])
    voice = _Voice()
    session.runtime.tts = voice  # type: ignore[assignment]

    session.spawn_respond("read plan.md and remember it")
    await asyncio.wait_for(voice.holding.wait(), 5)
    assert _replies(session) == [f"{reply}\n\n{MEMORY}"]  # what a hang-up would copy now

    session.played_samples = len(reply.encode()) // 2 + 1  # all of the reply, then the caveat
    await session.trigger_barge_in(b"\x00\x00" * 320)

    [entry] = [m for m in session.history if m["role"] == "assistant"]
    assert entry["content"] == f"{reply}\n\n{MEMORY}"  # heard whole: no "…"
    assert any(kept is entry for source_, kept in session.carried if source_ == "tool_result")
    voice.release.set()
    await session._cancel_active_turn()
    await memory.store.close()


@pytest.mark.parametrize("into_caveat", [0.0, 0.9], ids=["its start", "late in it"])
async def test_talking_over_the_caveat_keeps_it_with_a_reply_heard_whole(
    tmp_path: Path, into_caveat: float
) -> None:
    """The cut is measured against her words, not the caveat after them, and the caveat
    was shown: a "yes, do it" said over its end must meet it (mutations: measure against
    all the audio queued; drop it when talked over)."""
    reply = "I've saved that you like tea."
    memory, _kernel, session = await _session(tmp_path, [{"tokens": [reply]}])

    await workers.respond(session, "remember I like tea")
    words = len(reply.encode()) // 2
    session.played_samples = words + 1 + int(into_caveat * len(MEMORY.encode()) // 2)
    await session.trigger_barge_in(b"\x00\x00" * 320)

    assert _replies(session) == [f"{reply}\n\n{MEMORY}"]
    await session._cancel_active_turn()
    await memory.store.close()


async def test_the_next_reply_talked_over_doesnt_take_the_caveat_before_it(
    tmp_path: Path,
) -> None:
    """(mutation: a new reply keeps the last one's caveats)"""
    claim, plain = "I've saved that you like tea.", "Sure."
    memory, _kernel, session = await _session(tmp_path, [{"tokens": [claim]}, {"tokens": [plain]}])

    await workers.respond(session, "remember I like tea")
    await workers.respond(session, "thanks")
    session.played_samples = len(plain.encode()) // 2 + 1  # the second reply heard whole
    await session.trigger_barge_in(b"\x00\x00" * 320)

    assert _replies(session) == [f"{claim}\n\n{MEMORY}", plain]
    await session._cancel_active_turn()
    await memory.store.close()


async def test_a_reply_cut_short_loses_its_caveat_with_it(tmp_path: Path) -> None:
    """(mutation: keep the caveat after a reply cut short)"""
    reply = "I've saved that you like tea."
    memory, _kernel, session = await _session(tmp_path, [{"tokens": [reply]}])

    await workers.respond(session, "remember I like tea")
    session.played_samples = len(reply.encode()) // 4  # half of her words heard
    await session.trigger_barge_in(b"\x00\x00" * 320)

    [kept] = _replies(session)
    assert kept.endswith("…") and MEMORY not in kept
    await session._cancel_active_turn()
    await memory.store.close()


async def test_a_caveat_talked_over_before_it_was_shown_isnt_kept(tmp_path: Path) -> None:
    """Run outside the session's own task, so talking over it can't cancel it: it goes
    on, and must not put the caveat back after a reply cut short (mutation: keep it
    whatever happened while it was shown)."""
    reply = "I've saved that you like tea."
    memory, _kernel, session = await _session(tmp_path, [{"tokens": [reply]}])
    shown = session.emit_caveat

    async def talked_over(text: str) -> None:
        session.played_samples = len(reply.encode()) // 4  # half of her words heard
        await session.trigger_barge_in(b"\x00\x00" * 320)
        await shown(text)

    session.emit_caveat = talked_over  # type: ignore[method-assign]
    await asyncio.wait_for(asyncio.create_task(workers.respond(session, "remember I like tea")), 5)

    [kept] = _replies(session)
    assert kept.endswith("…") and MEMORY not in kept
    await session._cancel_active_turn()
    await memory.store.close()


async def test_a_caveat_that_cannot_be_said_leaves_the_reply_standing(tmp_path: Path) -> None:
    """It was shown, and stays after the reply (mutation: let the synthesis error
    through, which failed the whole turn)."""
    reply = "I've saved that you like tea."
    memory, _kernel, session = await _session(tmp_path, [{"tokens": [reply]}])
    session.runtime.tts = _Voice(fail=True)  # type: ignore[assignment]
    errors: list[str] = []

    async def seen(event: Event) -> None:
        errors.append(str(event.payload.get("kind", "")))

    session._bus.subscribe(VoiceEvent.error, seen)
    await workers.respond(session, "remember I like tea")

    assert errors == [] and _replies(session) == [f"{reply}\n\n{MEMORY}"]
    await memory.store.close()


class _HoldSecond:
    """Makes the first sentence at once and holds the second."""

    def __init__(self) -> None:
        self.made = 0
        self.holding = asyncio.Event()
        self.release = asyncio.Event()

    async def activate_voice(self, _voice_id: str, *, load: bool = True) -> None:
        return None

    def synthesize_stream(self, text: str, _voice_id: str) -> Any:
        async def gen() -> Any:
            self.made += 1
            if self.made == 2:
                self.holding.set()
                await self.release.wait()
            yield text.encode()

        return gen()


async def test_a_reply_cut_off_after_a_tool_ran_still_carries_what_she_read(
    tmp_path: Path,
) -> None:
    """Cut off mid-reply, the entry is new, and it may repeat what the tool returned
    (mutation: don't mark the entry an interruption keeps)."""
    source = tmp_path / "plan.md"
    source.write_text("Tea at four.", encoding="utf-8")
    memory, _kernel, session = await _session(
        tmp_path, [_read_call(source), {"tokens": ["Your plan says tea. ", "It is at four."]}]
    )
    voice = _HoldSecond()
    session.runtime.tts = voice  # type: ignore[assignment]
    session.spawn_respond("read plan.md")
    await asyncio.wait_for(voice.holding.wait(), 5)
    session.played_samples = session.ledger.total_samples  # the first sentence heard
    await session.trigger_barge_in(b"\x00\x00" * 320)

    [entry] = [m for m in session.history if m["role"] == "assistant"]
    assert any(kept is entry for source_, kept in session.carried if source_ == "tool_result")
    voice.release.set()
    await session._cancel_active_turn()
    await memory.store.close()
