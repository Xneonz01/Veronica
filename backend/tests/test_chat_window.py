"""What of a chat turn fits the model's window (`chat/window.py`): the persona and the
tool rules always, then the message, what she recalled, and the newest of the
conversation, opening on a question; and each tool round fitted again."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.adapters.llm.tokens import estimate
from app.core.event_bus import InProcessEventBus
from app.kernel.capability import CapabilityResult
from app.services.chat import window
from app.services.chat.replies import stream_reply
from app.services.chat.window import message_tokens
from app.services.tools import ToolBroker
from app.services.tools.render import render_result
from tests.fakes import ScriptedLLM, build_fake_kernel_runtime, build_fake_memory_runtime

PERSONA = "Persona."
SYSTEM = message_tokens({"content": PERSONA})


def _turns(count: int, words: int) -> list[dict[str, Any]]:
    """`count` question and reply pairs of `words` words, all the same size."""
    turns: list[dict[str, Any]] = []
    for index in range(count):
        turns += [
            {"role": "user", "content": f"q{index} " + "word " * words},
            {"role": "assistant", "content": f"a{index} " + "word " * words},
        ]
    return turns


def _asked(text: str = "next") -> dict[str, Any]:
    return {"role": "user", "content": text}


def _firsts(messages: list[dict[str, Any]]) -> list[str]:
    return [m["content"].split()[0] for m in messages[1:]]


def _room(*messages: dict[str, Any]) -> int:
    """Room for the persona and exactly these messages, and not one more token."""
    return SYSTEM + sum(map(message_tokens, messages))


def test_the_room_is_the_window_less_the_reply_and_the_tools() -> None:
    """(mutations: leave out the reply; leave out the tools)"""
    tools = [{"type": "function", "function": {"name": "memory_save", "about": "word " * 400}}]

    assert window.prompt_room(8192, 2048, window.tool_tokens([])) == 8192 - 2048 - estimate("[]")
    assert window.tool_tokens(tools) == estimate(json.dumps(tools))
    assert window.prompt_room(8192, 2048, 500) == 8192 - 2048 - 500


def test_every_message_costs_the_templates_own_tokens_too() -> None:
    """Twenty one-word messages are over a hundred tokens of role markers (mutation:
    count the words alone)."""
    assert message_tokens({"content": "hi"}) > estimate("hi")
    call = [{"function": {"name": "files_read", "arguments": {"path": "a.md"}}}]
    assert message_tokens({"content": "", "tool_calls": call}) >= estimate(json.dumps(call))


def test_a_message_that_fits_beside_the_persona_is_let_through() -> None:
    assert window.refusal(PERSONA, "What's on today?", 8192) is None


def test_a_message_that_cannot_fit_even_alone_is_sent_back() -> None:
    """Sent anyway, it would push the persona out (mutation: send what fits of it)."""
    question = "word " * 1000

    assert window.refusal(PERSONA, question, _room(_asked(question)) - 1) == "message_too_long"
    assert window.refusal(PERSONA, question, _room(_asked(question))) is None


def test_a_message_far_too_long_is_refused_by_its_length_without_being_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chat frame can be megabytes, and the check runs on the event loop (mutation:
    estimate the whole of it first)."""
    read: list[int] = []

    def counting(text: str) -> int:
        read.append(len(text))
        return estimate(text)

    monkeypatch.setattr(window, "estimate", counting)

    assert window.refusal(PERSONA, "word " * 400_000, 6144) == "message_too_long"
    assert max(read) < 2_000_000


def test_a_window_with_no_room_for_a_message_is_its_own_refusal() -> None:
    """Every message would be "too long": the settings are what to change (mutation:
    call it a message too long)."""
    room = _room(_asked("")) + window.MIN_MESSAGE_TOKENS

    assert window.refusal(PERSONA, "hi", room - 1) == "context_too_small"
    assert window.refusal(PERSONA, "hi", room) is None


def test_everything_fits_when_there_is_room() -> None:
    history = [*_turns(3, 10), _asked()]

    fitted = window.turn_messages(PERSONA, ["Memory.", "Documents."], history, 6000)

    assert fitted.messages[1:] == history and fitted.blocks == 2
    assert fitted.messages[0] == {"role": "system", "content": "Persona.\n\nMemory.\n\nDocuments."}


def test_the_oldest_turns_go_first_and_the_window_opens_on_a_question() -> None:
    """Room for the question and four of the messages, not a fifth (mutation: keep the
    oldest)."""
    history = [*_turns(4, 200), _asked()]
    room = _room(*history[-5:]) + message_tokens(history[0]) - 1

    fitted = window.turn_messages(PERSONA, [], history, room)

    assert _firsts(fitted.messages) == ["q2", "a2", "q3", "a3", "next"]


def test_a_cut_on_a_reply_opens_on_the_next_question() -> None:
    history = [*_turns(2, 200), _asked()]
    room = _room(*history[-4:])  # the oldest message it holds would be a reply

    fitted = window.turn_messages(PERSONA, [], history, room)

    assert _firsts(fitted.messages) == ["q1", "a1", "next"]


def test_what_she_recalled_takes_room_from_the_conversation() -> None:
    """(mutation: leave the blocks out of the count)"""
    history = [*_turns(4, 200), _asked()]
    memory = "text " * 410  # nearly two messages' worth: a four-letter word is a token
    room = _room(*history[-5:])

    fitted = window.turn_messages(PERSONA, [memory, ""], history, room)

    assert _firsts(fitted.messages) == ["q3", "a3", "next"]
    assert fitted.messages[0]["content"] == f"Persona.\n\n{memory}"  # never trimmed
    assert fitted.blocks == 2  # an empty block is no loss


def test_documents_give_way_to_a_long_message_before_memory_does() -> None:
    """The message is what was asked; what she recalled for it comes after, memory
    first (mutations: keep a block that doesn't fit; skip to a later one)."""
    question = _asked("word " * 900)
    memory, documents = "memory " * 50, "doc " * 200
    room = _room(question) + estimate(memory) + 20

    fitted = window.turn_messages(PERSONA, [memory, documents], [question], room)

    assert fitted.blocks == 1
    assert fitted.messages[0]["content"] == f"Persona.\n\n{memory}"
    later = window.turn_messages(PERSONA, [documents, memory], [question], room)
    assert later.blocks == 0 and later.messages[0]["content"] == PERSONA


def _call(results: int) -> dict[str, Any]:
    call = {"function": {"name": "files_read", "arguments": {"path": "a.md"}}}
    return {"role": "assistant", "content": "", "tool_calls": [call] * results}


def _prompt(history: list[dict[str, Any]], room: int) -> window.TurnWindow:
    return window.TurnWindow(window.turn_messages(PERSONA, [], history, room).messages, room)


def test_a_rounds_results_share_the_room_the_older_conversation_can_make() -> None:
    """(mutation: budget only what is free now, and a full window leaves nothing)"""
    history = [*_turns(4, 200), _asked()]
    room = _room(*history)
    turn = _prompt(history, room)
    turn._messages.append(_call(2))

    budget = turn.result_budget(2)

    free = room - SYSTEM - message_tokens(history[-1]) - message_tokens(_call(2))
    assert budget == (free - 2 * message_tokens({"content": ""})) // 2 and budget > 700


def test_after_a_round_the_oldest_turns_make_way_and_it_still_opens_on_a_question() -> None:
    """(mutations: send the round without making room; leave a reply opening it)"""
    history = [*_turns(4, 200), _asked()]
    room = _room(*history)
    turn = _prompt(history, room)
    messages = turn._messages
    messages += [_call(1), {"role": "tool", "content": "text " * 600}]

    turn.make_room()

    assert sum(map(message_tokens, messages)) <= room
    assert messages[0]["content"] == PERSONA and messages[1]["role"] == "user"
    kept = messages[1:-2]
    assert 1 < len(kept) < len(history) and kept == history[-len(kept) :]  # the newest


def test_a_reply_whose_question_made_way_goes_too() -> None:
    """A round that needs one message's room drops the oldest question: its reply
    would then open the window (mutation: leave the reply)."""
    history = [*_turns(4, 200), _asked()]
    room = _room(*history)
    turn = _prompt(history, room)
    turn._messages += [_call(1), {"role": "tool", "content": "text " * 120}]

    turn.make_room()

    assert [m["role"] for m in turn._messages[:2]] == ["system", "user"]
    assert _firsts(turn._messages[:-2]) == ["q1", "a1", "q2", "a2", "q3", "a3", "next"]


def test_the_question_is_found_by_identity_not_by_its_words() -> None:
    """The same question asked twice: the earlier one can make way, the turn's can't
    (mutation: find it by equality)."""
    history = [_asked(), {"role": "assistant", "content": "word " * 300}, _asked()]
    turn = _prompt(history, 2000)
    turn._room = _room(history[-1]) + 250
    turn._messages += [_call(1), {"role": "tool", "content": "text " * 150}]

    turn.make_room()

    assert [m["role"] for m in turn._messages] == ["system", "user", "assistant", "tool"]


def _read_result(content: str) -> CapabilityResult:
    return CapabilityResult(ok=True, data={"content": content})


def test_a_result_within_its_budget_is_whole() -> None:
    rendered = render_result("files.read", _read_result("Short notes."), budget=500)

    assert "Short notes." in rendered and "truncated" not in rendered


def test_a_result_over_its_budget_is_cut_inside_its_fence() -> None:
    """(mutations: ignore the budget; count the fence as one text)"""
    rendered = render_result("files.read", _read_result("text " * 2000), budget=300)

    assert "… (truncated)\n<<end result " in rendered
    for budget in range(100, 1200, 7):
        for content in ("text " * 2000, "line\n" * 900, "1 2 3 " * 900):
            cut = render_result("files.read", _read_result(content), budget=budget)
            assert estimate(cut) <= budget


def test_a_result_with_no_room_left_says_so() -> None:
    rendered = render_result("files.read", _read_result("text " * 2000), budget=10)

    assert "no room left in this conversation" in rendered and "text text" not in rendered


async def test_a_tool_round_never_pushes_the_persona_out(tmp_path: Path) -> None:
    """A long conversation, then a long file read: what the model gets next still fits,
    persona first (mutation: no window for the rounds)."""
    note = tmp_path / "notes.md"
    note.write_text("line of notes " * 500, encoding="utf-8")  # under the 8,000-character cap
    memory = await build_fake_memory_runtime(tmp_path / "db", InProcessEventBus())
    kernel = await build_fake_kernel_runtime(
        memory, InProcessEventBus(), file_roots=[str(tmp_path)]
    )
    broker = ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")
    read = {"function": {"name": "files_read", "arguments": {"path": str(note)}}}
    llm = ScriptedLLM([{"tool_calls": [read]}, {"tokens": ["Done."]}])
    history = [*_turns(6, 100), _asked()]
    room = _room(*history)
    messages = window.turn_messages(PERSONA, [], history, room).messages

    async def ignore(*_args: Any) -> None:
        return None

    await stream_reply(
        llm,  # type: ignore[arg-type]
        broker,
        messages,
        model="test",
        send=ignore,
        on_tool=ignore,
        window=window.TurnWindow(messages, room),
    )

    second = llm.calls[1]
    assert sum(map(message_tokens, second)) <= room
    assert second[0]["content"] == PERSONA and second[-1]["role"] == "tool"
    assert "… (truncated)" in second[-1]["content"]
    await memory.store.close()
