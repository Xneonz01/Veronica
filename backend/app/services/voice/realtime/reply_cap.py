"""How much one spoken reply may say, enforced where the model's text is read.

`LlmSettings.num_predict` stops a runaway generation on every lane, and it has to sit
above the longest thing chat legitimately writes, so on a call it still allows minutes
of speech. A spoken reply is heard at about eighteen characters a second, and a model
caught in a loop would talk until the user cut in. `RealtimeSettings.reply_max_chars`
is the call's own ceiling.

It is counted here, in characters, rather than sent to the model as a token limit:

* A token limit cuts wherever the count lands, mid-word or mid-number, and the chunker
  would hand that fragment to the synthesiser as a finished sentence. Counted here, the
  reply ends after a sentence, by the chunker's own rule for where one ends.
* Characters are what the listener pays for, in seconds.
* A count of what was read works with any model behind the provider interface.

Past the ceiling by a quarter with no sentence ending (a loop writes no full stops),
the reply is cut at a word. The model's stream is then closed, not just left: closing
it is what stops the generation.

And it is counted as the text arrives, not as it is spoken (`read_ahead`). `respond`
synthesises each sentence before it reads on, so it reads the model at the pace of
speech. Counted at that pace, a runaway reply would be stopped only after the speaker
had worked through the ceiling's worth of it, the model running all that time on the
GPU the synthesiser needs. Read ahead, it is stopped as soon as it has written enough.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass

from app.adapters.llm.types import LlmChunk
from app.services.voice.realtime.chunker import sentence_end


@dataclass
class ReplyBudget:
    """One turn's allowance, shared by every model call the turn makes. 0 is no ceiling.

    A turn with tool rounds is several model calls and one reply: what she says before
    a lookup counts against the same ceiling as the answer after it.
    """

    max_chars: int
    spent: int = 0
    capped: bool = False

    @property
    def hard_stop(self) -> int:
        """Where a reply with no sentence ending in sight is cut anyway."""
        return self.max_chars + self.max_chars // 4


def read_reply(
    stream: AsyncIterator[LlmChunk], budget: ReplyBudget
) -> aclosing[AsyncGenerator[LlmChunk, None]]:
    """A model call as the call reads it: held to `budget`, read ahead of the speaker,
    and closed however the reader leaves it (`async with`).

    Closed, not left for the garbage collector: a barge-in cancels the turn mid-read,
    and the close is what stops the model generating for nobody.
    """
    return aclosing(read_ahead(within(stream, budget)))


async def within(
    stream: AsyncIterator[LlmChunk], budget: ReplyBudget
) -> AsyncGenerator[LlmChunk, None]:
    """`stream`, ended after the first sentence that finishes past `budget`'s ceiling.

    Chunks with no content (the terminal one carries the tool calls) pass through
    untouched, so a reply inside its allowance is exactly the stream it would have been.
    Past the ceiling, text goes on a whole word at a time: the word still being written is
    held until the next delta shows it finished, so a cut never leaves half a word to say.
    """
    said = ""  # this call's text passed on: a sentence end can span two deltas
    held = ""  # past the ceiling: arrived, not yet known to be whole words
    try:
        async for chunk in stream:
            text = chunk.content
            if not text or budget.max_chars <= 0:
                if held and chunk.done:  # the call is over, so the held word was whole
                    budget.spent += len(held)
                    yield LlmChunk(content=held)
                    said, held = said + held, ""
                yield chunk
                continue
            if budget.capped:
                return
            if not held and budget.spent + len(text) <= budget.max_chars:
                budget.spent += len(text)
                said += text
                yield chunk
                continue
            out, held, done = _past_the_ceiling(said + held + text, len(said), budget)
            if out:
                budget.spent += len(out)
                said += out
                yield LlmChunk(content=out)
            if done:
                budget.capped = True
                return
        if held:  # a stream that ended without its terminal chunk
            budget.spent += len(held)
            yield LlmChunk(content=held)
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


def _past_the_ceiling(joined: str, start: int, budget: ReplyBudget) -> tuple[str, str, bool]:
    """What of `joined[start:]` to pass on, what to hold, and whether the reply ends here.

    It ends after the first sentence that finishes at or past the ceiling, looked for
    across the join: a full stop that ended the last delta is only known to end a sentence
    by the space that opens this one. A quarter past the ceiling with none finishing, it
    ends at the last sentence end, or where the text already passed on ended one, or else
    at the last whole word.
    """
    ceiling = start + max(0, budget.max_chars - budget.spent)
    stop = start + budget.hard_stop - budget.spent
    end = sentence_end(joined, ceiling)
    if end is not None and end <= stop:
        return joined[start:end], "", True
    if len(joined) > stop:
        end = _last_sentence_end(joined, start, stop)
        if end is None and not _ends_a_sentence(joined[:start]):
            end = _last_space(joined, start, stop)
        return (joined[start:end] if end is not None else ""), "", True
    space = _last_space(joined, start, len(joined) - 1)
    if space is None:
        return "", joined[start:], False
    return joined[start:space], joined[space:], False


def _last_sentence_end(text: str, start: int, stop: int) -> int | None:
    """Where the last sentence finishing between `start` and `stop` ends."""
    last = None
    end = sentence_end(text, start)
    while end is not None and end <= stop:
        last = end
        end = sentence_end(text, end + 1)
    return last


def _ends_a_sentence(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and sentence_end(f"{stripped} ", len(stripped)) is not None


def _last_space(text: str, start: int, stop: int) -> int | None:
    """The last whitespace in `text[start : stop + 1]`: where whole words end."""
    for index in range(min(stop, len(text) - 1), start - 1, -1):
        if text[index].isspace():
            return index
    return None


async def read_ahead(stream: AsyncIterator[LlmChunk]) -> AsyncGenerator[LlmChunk, None]:
    """`stream`, read as fast as it arrives, whatever the pace it is consumed at.

    A task drains `stream` into a queue and this yields from the queue, so `within`
    counts the reply when the model writes it, not when the speaker reaches it. Order
    and the terminal chunk are kept. Closing this cancels the task and closes `stream`
    behind it.
    """
    queue: asyncio.Queue[LlmChunk | Exception | None] = asyncio.Queue()

    async def drain() -> None:
        try:
            async for chunk in stream:
                queue.put_nowait(chunk)
        except Exception as exc:
            # Carried, not swallowed: a model that fails mid-reply (unreachable, a
            # stream error) has to fail the turn in `respond`, not end it as if the
            # reply were complete.
            queue.put_nowait(exc)
            return
        queue.put_nowait(None)

    reader = asyncio.create_task(drain())
    try:
        while True:
            item = await queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        reader.cancel()
        # `gather` rather than suppressing CancelledError around an await: the reader's
        # own cancellation comes back as a value and is dropped, while a cancellation
        # aimed at this task still propagates.
        await asyncio.gather(reader, return_exceptions=True)
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
