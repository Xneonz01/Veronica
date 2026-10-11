"""The spoken reply's ceiling: what passes untouched, where it ends, and that it closes.

`within` and `read_ahead` are async generators over `LlmChunk`s, so most cases are a
scripted model call with a known answer. The last three go through the real `respond`,
because a ceiling that is right in isolation and not on the path a call takes protects
nothing.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.adapters.llm.ollama_errors import OllamaStreamError
from app.adapters.llm.types import LlmChunk
from app.services.voice.realtime import workers
from app.services.voice.realtime.reply_cap import ReplyBudget, read_ahead, within
from tests.fakes import FakeDetector, FakeSTT, FakeTTS, FakeWS, ListMetricsSink
from tests.test_realtime_session import make_session, wait_until
from tests.test_voice_tools import _read_call, _spoken
from tests.test_voice_tools import _session as tools_session

RUNAWAY = ["This is one more sentence of a reply that will not stop. "] * 60


class _Model:
    """A model call that records how far it was read and whether it was closed."""

    def __init__(
        self,
        deltas: list[str],
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        hold: bool = False,
    ) -> None:
        self._deltas = deltas
        self._tool_calls = tool_calls
        self._hold = hold
        self.read = 0
        self.closed = False
        self.finished = False

    async def stream_chat(
        self, messages: Any, tools: Any = None, *, model: Any = None, keep_alive: Any = None
    ) -> AsyncIterator[LlmChunk]:
        try:
            for delta in self._deltas:
                self.read += 1
                yield LlmChunk(content=delta)
            if self._hold:
                await asyncio.Event().wait()  # still writing when the turn is cut off
            self.finished = True
            yield LlmChunk(done=True, tool_calls=self._tool_calls, done_reason="stop")
        finally:
            self.closed = True

    def stream(self) -> AsyncIterator[LlmChunk]:
        return self.stream_chat([])


def _tokens(text: str) -> list[str]:
    """`text` the way a model streams it: a word a delta, and every mark on its own."""
    return re.findall(r"\s*[^\s.,!?]+|[.,!?]", text)


async def _said(model: _Model, budget: ReplyBudget) -> tuple[str, list[LlmChunk]]:
    chunks = [chunk async for chunk in within(model.stream(), budget)]
    return "".join(chunk.content or "" for chunk in chunks), chunks


async def test_a_reply_inside_its_allowance_is_the_stream_it_would_have_been() -> None:
    """The ceiling costs an ordinary turn nothing: the same deltas, and the terminal
    chunk, which carries the tool calls the loop turns on, untouched."""
    calls = [{"function": {"name": "files_read", "arguments": {"path": "note.txt"}}}]
    model = _Model(["Let me ", "check that."], tool_calls=calls)
    budget = ReplyBudget(900)

    said, chunks = await _said(model, budget)

    assert said == "Let me check that."
    assert chunks[-1].done and chunks[-1].tool_calls == calls
    assert budget.spent == len(said) and budget.capped is False
    assert model.finished and model.closed


async def test_a_reply_past_its_allowance_ends_after_the_sentence() -> None:
    """Not where the count lands: "...and ask" would be spoken as a finished sentence.

    mutation: `return` as soon as the ceiling is crossed; the reply ends "...and ask".
    """
    model = _Model(_tokens("Review your plan carefully and ask early. Then do more."))
    budget = ReplyBudget(36)  # crossed at " early", five characters short of the stop

    said, _ = await _said(model, budget)

    assert said == "Review your plan carefully and ask early."
    assert budget.capped is True
    assert model.closed and not model.finished, "the model was left generating"


async def test_a_decimal_point_is_not_where_a_reply_ends() -> None:
    """The model streams "3", "." and "72"; ending at the first full stop would have
    her say the total is three."""
    model = _Model(_tokens("Your total comes to 3.72. That is fine."))
    budget = ReplyBudget(21)  # crossed at the decimal point itself

    said, _ = await _said(model, budget)

    assert said == "Your total comes to 3.72."


async def test_an_abbreviation_is_not_where_a_reply_ends() -> None:
    """The chunker's own rule, shared: "Dr." does not end a sentence, so a reply capped
    there runs on to the real end rather than stopping at "ask Dr."."""
    text = (
        "If the results worry you, and I can see why they might, you could ask Dr. Patel "
        "about it. Then book a time."
    )
    after_dr = text.index("Dr.") + len("Dr.")
    budget = ReplyBudget(after_dr - 1)  # crossed at the abbreviation's full stop

    said, _ = await _said(_Model(_tokens(text)), budget)

    assert said == text[: text.index("it.") + len("it.")]


async def test_a_reply_that_never_ends_a_sentence_is_cut_anyway() -> None:
    """The case the ceiling exists for: a model in a loop writes no full stops. A
    quarter past the ceiling it is cut at a word.

    mutation: drop the `hard_stop` test; all two hundred deltas are read.
    """
    model = _Model(["word "] * 200)
    budget = ReplyBudget(100)

    said, _ = await _said(model, budget)

    assert 100 <= len(said) <= budget.hard_stop
    assert budget.capped is True
    assert model.read < 40 and model.closed


async def test_one_allowance_covers_every_model_call_of_the_turn() -> None:
    """A tool turn is several calls and one reply. Once the first has used the
    allowance, the next is closed on its first word rather than spoken."""
    budget = ReplyBudget(30)
    first, _ = await _said(_Model(_tokens("Let me look that up for you. One moment.")), budget)
    later = _Model(_tokens("Here is a great deal more."))

    second, _ = await _said(later, budget)

    assert first == "Let me look that up for you."
    assert second == "" and later.closed and later.read == 1


async def test_a_model_that_streams_a_sentence_a_delta_loses_only_the_overrun() -> None:
    """A delta that crosses the ceiling and the margin at once is cut back to whole
    words inside the margin, not dropped with everything she was about to say. "full"
    ends on the margin itself and is kept: the space after it shows the word is whole.

    mutation: `_past_the_ceiling` passing nothing on at the hard stop; nothing is said.
    """
    model = _Model(["This one delta is a long run of words with no full stop anywhere in it"])
    budget = ReplyBudget(40)

    said, _ = await _said(model, budget)

    assert said == "This one delta is a long run of words with no full"
    assert len(said) <= budget.hard_stop and budget.capped is True


async def test_the_reply_ends_in_the_sentence_the_ceiling_falls_in_not_one_before() -> None:
    """A delta that crosses the ceiling can finish a sentence short of it. The reply runs
    on to the end of the sentence the ceiling falls in, as it would a word a delta.

    mutation: look for the sentence end from where the delta starts; the reply ends
    "...Short one." with part of its allowance unspent.
    """
    deltas = [
        "Your plan for the week has four parts, and the first two can start today.",
        " Short one. Then the ceiling falls. Third.",
    ]
    budget = ReplyBudget(len(deltas[0]) + len(" Short one. Then the"))

    said, _ = await _said(_Model(deltas), budget)

    assert said == f"{deltas[0]} Short one. Then the ceiling falls."
    assert len(said) <= budget.hard_stop and budget.capped is True


async def test_a_sentence_running_past_the_margin_ends_the_reply_at_the_one_before() -> None:
    """The sentence the ceiling falls in finishes past the hard stop. The reply ends at
    the last sentence that finished inside the margin, not at a word in the next one.

    mutation: skip the last sentence end at the hard stop; "Short one." is lost.
    """
    deltas = [
        "Your plan for the week has four parts, and the first two can start today.",
        " Short one. Then the ceiling falls somewhere in this long sentence. Third.",
    ]
    budget = ReplyBudget(len(deltas[0]) + len(" Short one. Then the"))

    said, _ = await _said(_Model(deltas), budget)

    assert said == f"{deltas[0]} Short one."


async def test_a_reply_cut_at_the_hard_stop_ends_on_the_sentence_already_said() -> None:
    """The last delta passed on ended a sentence, and the next runs past the margin with
    none. Cut at a word, "five" would be spoken as a sentence of its own.

    mutation: drop the check on what was already said; the reply ends "four. five".
    """
    model = _Model(["One two three four. ", "five six seven eight nine ten eleven"])
    budget = ReplyBudget(20)

    said, _ = await _said(model, budget)

    assert said.strip() == "One two three four."


async def test_a_cut_past_the_ceiling_never_leaves_half_a_word() -> None:
    """Past the ceiling a delta can end mid-word ("c", then "ccc"). Passed on as it came,
    the cut at the hard stop left the "c" to be spoken as a word of its own.

    mutation: pass the margin's deltas on whole; the reply ends "aaaa bbbb c".
    """
    model = _Model(["aaaa bbbb", " c", "ccc", " dddd"])
    budget = ReplyBudget(10)

    said, _ = await _said(model, budget)

    assert said == "aaaa bbbb"
    assert budget.capped is True and model.closed


async def test_a_word_held_at_the_end_of_the_call_is_still_said() -> None:
    """Held past the ceiling, the last word is said when the call ends inside the margin:
    the end of the call is what shows the word was whole.

    mutation: drop the flush on the terminal chunk; "done" comes after the call's end.
    """
    model = _Model(["aaaa bbbb", " done"])
    budget = ReplyBudget(12)  # " done" is past the ceiling and inside the margin

    said, chunks = await _said(model, budget)

    assert said == "aaaa bbbb done"
    assert chunks[-1].done and budget.capped is False


async def test_zero_is_no_ceiling() -> None:
    model = _Model(["word "] * 50)
    said, _ = await _said(model, ReplyBudget(0))
    assert len(said) == 250 and model.finished


# --- read ahead of the speaker ---------------------------------------------------------


async def test_the_model_is_stopped_when_it_has_written_enough_not_when_it_is_said() -> None:
    """The consumer takes one delta and then stops to "speak". Read ahead, the model is
    closed while the consumer is still on that first delta."""
    model = _Model(["word "] * 400)
    budget = ReplyBudget(100)
    speaking = read_ahead(within(model.stream(), budget))

    first = await anext(speaking)
    for _ in range(50):  # she is synthesising; the loop turns and the reader runs
        await asyncio.sleep(0)

    assert first.content == "word "
    assert model.closed and model.read < 40, (model.closed, model.read)
    rest = [chunk.content or "" async for chunk in speaking]
    assert len("".join([first.content, *rest])) <= budget.hard_stop


async def test_a_model_that_fails_mid_reply_still_fails_the_turn() -> None:
    """The stream is read on a task nobody awaits, so its error has to be carried to
    the reader. Dropped, a model that died mid-sentence would look like a finished reply.

    mutation: `queue.put_nowait(exc)` to `queue.put_nowait(None)` in `drain`.
    """

    async def dies() -> AsyncIterator[LlmChunk]:
        yield LlmChunk(content="Your plan")
        yield LlmChunk(content=" has")
        raise OllamaStreamError("model evicted")

    heard: list[str] = []
    with pytest.raises(OllamaStreamError, match="evicted"):
        async for chunk in read_ahead(dies()):
            heard.append(chunk.content or "")
    assert heard == ["Your plan", " has"]


async def test_closing_mid_reply_leaves_nothing_reading_the_model() -> None:
    """Closed while the reader waits on a slow model, the task goes and the model's
    stream is closed, without waiting for the model.

    mutation: drop `reader.cancel()`; the close waits on a reader that waits forever.
    """
    model = _Model(["One"], hold=True)
    speaking = read_ahead(model.stream())
    assert (await anext(speaking)).content == "One"

    await asyncio.wait_for(speaking.aclose(), timeout=2.0)

    assert model.closed


# --- on the path a call takes ----------------------------------------------------------


class _HeldTTS(FakeTTS):
    """Holds the first sentence mid-synthesis until released, as a slow voice would."""

    def __init__(self) -> None:
        super().__init__()
        self.speaking = asyncio.Event()
        self.release = asyncio.Event()
        self.said: list[str] = []

    def synthesize_stream(self, text: str, _voice_id: str) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            self.said.append(text)
            self.speaking.set()
            await self.release.wait()
            yield text.encode()

        return gen()


async def test_a_runaway_reply_is_stopped_while_she_is_still_on_its_first_sentence() -> None:
    """Through `respond`: the model is closed once it has written the ceiling's worth,
    while the first sentence is still being made. She then says up to the end of the
    sentence the ceiling fell in, and the turn's row says it was cut.

    mutation: `read_reply` without `read_ahead`; the model is read at the speaker's
    pace, still open on its first delta while that sentence is synthesised.
    """
    model = _Model(RUNAWAY)
    tts = _HeldTTS()
    sink = ListMetricsSink()
    session = make_session(
        FakeWS(), detector=FakeDetector([]), stt=FakeSTT([], None), tts=tts, llm=model, sink=sink
    )  # type: ignore[arg-type]
    turn = asyncio.create_task(workers.respond(session, "tell me everything"))
    try:
        await wait_until(tts.speaking.is_set)
        await wait_until(lambda: model.closed)
        assert len(tts.said) == 1 and not model.finished and model.read < len(RUNAWAY)
    finally:
        tts.release.set()
    await turn

    ceiling = session.settings.realtime.reply_max_chars
    spoken = " ".join(tts.said)
    assert ceiling <= len(spoken) <= ceiling + ceiling // 4
    assert spoken.endswith("will not stop.")
    assert sink.rows[-1]["reply_capped"] is True


async def test_a_runaway_answer_after_a_tool_is_cut_the_same_way(tmp_path: Path) -> None:
    """The tool loop reads each model call through the same ceiling.

    mutation: `tool_loop` reading `stream_chat` directly; all sixty sentences are said.
    """
    note = tmp_path / "note.txt"
    note.write_text("the roof needs fixing", encoding="utf-8")
    _ws, (memory, _kernel), session = await tools_session(
        tmp_path, [_read_call(note), {"tokens": RUNAWAY}]
    )

    await workers.respond(session, "what does my note say?")

    ceiling = session.settings.realtime.reply_max_chars
    reply = session.history[-1]["content"]
    assert ceiling <= len(reply) <= ceiling + ceiling // 4
    assert _spoken(session).count("will not stop.") == reply.count("will not stop.")
    assert session._metrics_sink.rows[-1]["reply_capped"] is True  # type: ignore[attr-defined]
    await memory.store.close()


async def test_cutting_her_off_closes_the_model_before_the_turn_is_gone() -> None:
    """A barge-in cancels the turn while she is making a sentence and the model is still
    writing. The model's stream is closed by the time the cancelled turn has finished,
    not whenever the garbage collector reaches the reader.

    Cut off mid-synthesis, not while waiting on the model: there the cancellation lands
    inside the reader's own `finally` and closes it either way. Mid-synthesis, where a
    turn spends most of its time, only the `async with` closes it.

    mutation: `read_reply` with `contextlib.nullcontext` in place of `aclosing`.
    """
    model = _Model(["Here is the first part of it. "], hold=True)
    tts = _HeldTTS()
    session = make_session(
        FakeWS(),
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=tts,
        llm=model,  # type: ignore[arg-type]
        sink=ListMetricsSink(),
    )
    turn = asyncio.create_task(workers.respond(session, "go on"))
    await wait_until(tts.speaking.is_set)

    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    assert model.closed
