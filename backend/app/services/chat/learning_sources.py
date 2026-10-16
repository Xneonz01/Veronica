"""Where learning reads what was said: chat conversations (`learning_queue.py`, whose
words stay in `chat_messages`) and finished calls (`memory/call_queue.py`, whose words
are kept until read). The worker (`learning_worker.py`) treats both alike: it takes the
one due, reads the part of it one extraction takes, and says how that went.

It reads only what was said. Tool rows stay out, and so does a chat reply that quoted a
document (a cited one) or followed a tool that returned content (a page, a file, a
recall): the extractor can't tell that content from the user talking about themselves,
and a page's line she passed on would be filed as the user's own. A call keeps only its
spoken turns to begin with, without the ones that carried the web (`session_tasks.py`).

A part after the first comes with the last turns read before it (`Part.earlier`), so
that "she's vegetarian" after a pause has someone to be (bug 40). They are context:
what the extractor takes from them anyway isn't filed (`LearningController.file`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import NamedTuple, Protocol, TypeVar

from app.services.chat.learning_queue import Due, LearningQueue
from app.services.chat.store import ChatStore, StoredMessage
from app.services.memory.call_queue import CallDue, CallQueue
from app.services.tools.sources import returns_content

_PAGE = 200  # messages read per extraction, at most
_LABEL = 12  # what the transcript adds to a turn: "User: " or "Veronica: " and a newline
# What a part is sent with: the last two exchanges already read at most, in at most a
# quarter of what one extraction reads. Tool rows don't count, so a conversation's last
# `_READ_ROWS` messages are looked through for them.
_EARLIER_TURNS = 4
_EARLIER_SHARE = 4
_READ_ROWS = 20

Said = list[dict[str, str]]


class Part(NamedTuple):
    """What one extraction reads of something due."""

    said: Said  # the turns to learn from
    through: int  # the position they take it to
    earlier: Said  # the last turns read before them: context, not learned from again


class Pending(Protocol):
    """What the worker reads of anything due: how its last attempts went."""

    @property
    def attempts(self) -> int: ...

    @property
    def last_error(self) -> str | None: ...


PendingT = TypeVar("PendingT", bound=Pending)


class Source(Protocol[PendingT]):
    """One kind of thing learning reads."""

    async def next_due(self) -> PendingT | None:
        """What has waited longest and isn't backing off."""
        ...

    async def part(self, due: PendingT, max_chars: int) -> Part:
        """What one extraction reads of `due`, the turns before it included, in at most
        `max_chars`."""
        ...

    async def kept(self, due: PendingT) -> bool:
        """Whether it is still there, so that what was learned from it may be filed."""
        ...

    async def done(self, due: PendingT, *, through: int) -> None: ...

    async def failed(self, due: PendingT, error: str) -> int:
        """Try again later, saying why; the wait in seconds."""
        ...


def pack(said: Iterable[tuple[int, str, str]], max_chars: int) -> tuple[Said, int]:
    """The turns one extraction reads of `said` (a position, a role, and the words, or
    none for what isn't read), and the position it read through.

    It stops before a turn that would take the transcript past `max_chars`, labels
    included, and that turn waits for the next extraction. A first turn longer than that
    on its own is cut to it. The position is 0 when there was nothing.
    """
    turns: Said = []
    through = 0
    used = 0
    for position, role, text in said:
        if text and turns and used + len(text) + _LABEL > max_chars:
            break
        through = position
        if text:
            text = text[: max(1, max_chars - _LABEL)]
            turns.append({"role": role, "content": text})
            used += len(text) + _LABEL
    return turns, through


def earlier(read: Sequence[tuple[str, str]], max_chars: int) -> Said:
    """What a part read after `read` (a role and the words of each turn already read) is
    sent with: of the last `_EARLIER_TURNS`, those that fit whole in a quarter of
    `max_chars`, the newest first, in the order they were said. A turn too long to fit
    is left out, not cut: a line cut short can say something it didn't."""
    room = max_chars // _EARLIER_SHARE
    kept: Said = []
    for role, text in reversed(read[-_EARLIER_TURNS:]):
        if len(text) + _LABEL <= room:
            kept.append({"role": role, "content": text})
            room -= len(text) + _LABEL
    return kept[::-1]


def size(turns: Said) -> int:
    """The characters `turns` take in a transcript, labels included."""
    return sum(len(turn["content"]) + _LABEL for turn in turns)


def beside(before: Said, first: str | None, max_chars: int) -> Said:
    """`before`, unless the part's first turn (`first`, its words) would then be cut: it
    has the room first. A turn cut short is never read past its cut."""
    if first is not None and len(first) + _LABEL > max_chars - size(before):
        return []
    return before


def unread(
    messages: list[StoredMessage], max_chars: int, *, after_content: bool = False
) -> tuple[Said, int]:
    """What one extraction reads of a conversation's `messages` (`pack`), and the id of
    the message it read through."""
    said = heard(messages, after_content=after_content)
    return pack(((m.id, m.role, m.content if m.id in said else "") for m in messages), max_chars)


def heard(messages: Sequence[StoredMessage], *, after_content: bool = False) -> set[int]:
    """The ids of the `messages` learning reads, in order: what the user said, and her
    replies that neither quoted a document nor followed a tool that returned content.
    `after_content`: the rows before them ended on such a tool, so the first reply
    follows it."""
    said: set[int] = set()
    for m in messages:
        if m.role == "tool":
            after_content = after_content or returns_content(m.tool_name)
            continue
        if m.content.strip() and (
            m.role == "user" or (m.role == "assistant" and not m.citations and not after_content)
        ):
            said.add(m.id)
        if m.role in ("user", "assistant"):
            after_content = False
    return said


def ends_on_content(read: Sequence[StoredMessage]) -> bool:
    """Whether `read` ends on a tool that returned content, no turn after it. A part
    can stop between such a tool and her reply to it (`pack` moves past the rows that
    hold no words), and that reply must not open the next part as her own words."""
    after = False
    for m in read:
        if m.role == "tool":
            after = after or returns_content(m.tool_name)
        elif m.role in ("user", "assistant"):
            after = False
    return after


def _earlier_of(read: Sequence[StoredMessage], max_chars: int) -> Said:
    said = heard(read)
    return earlier([(m.role, m.content) for m in read if m.id in said], max_chars)


class ChatSource:
    """Chat conversations: the queue's markers, the words read from the store."""

    def __init__(self, queue: LearningQueue, chat: ChatStore) -> None:
        self._queue = queue
        self._chat = chat

    async def next_due(self) -> Due | None:
        return await self._queue.next_due()

    async def part(self, due: Due, max_chars: int) -> Part:
        messages = await self._chat.messages_after(
            due.conversation_id, due.learned_through, limit=_PAGE
        )
        read = await self._read(due)
        seed = ends_on_content(read)
        said = heard(messages, after_content=seed)
        first = next((m.content for m in messages if m.id in said), None)
        before = beside(_earlier_of(read, max_chars), first, max_chars) if first else []
        turns, through = unread(messages, max_chars - size(before), after_content=seed)
        return Part(turns, through or due.learned_through, before)

    async def _read(self, due: Due) -> list[StoredMessage]:
        """The last rows already read, `_READ_ROWS` at most; none before the first part."""
        if not due.learned_through:
            return []
        return await self._chat.messages_through(
            due.conversation_id, due.learned_through, limit=_READ_ROWS
        )

    async def kept(self, due: Due) -> bool:
        # Deleted while the model read it: nothing of it is filed.
        return await self._chat.get(due.conversation_id) is not None

    async def done(self, due: Due, *, through: int) -> None:
        await self._queue.done(due, through=through)

    async def failed(self, due: Due, error: str) -> int:
        return await self._queue.failed(due, error)


class CallSource:
    """Finished calls, whose turns are kept until learning has read them all."""

    def __init__(self, calls: CallQueue) -> None:
        self._calls = calls

    async def next_due(self) -> CallDue | None:
        return await self._calls.next_due()

    async def part(self, due: CallDue, max_chars: int) -> Part:
        turns, total = await self._calls.turns(due)
        read, rest = turns[: due.learned_through], turns[due.learned_through :]
        if not rest:
            return Part([], total, [])  # read to its end, or unreadable: `done` lets it go
        before = earlier([(turn["role"], turn["content"]) for turn in read], max_chars)
        before = beside(before, rest[0]["content"], max_chars)
        numbered = ((n, turn["role"], turn["content"]) for n, turn in enumerate(rest, start=1))
        said, count = pack(numbered, max_chars - size(before))
        return Part(said, due.learned_through + count, before)

    async def kept(self, due: CallDue) -> bool:
        return True  # nothing takes a finished call away before it has been read

    async def done(self, due: CallDue, *, through: int) -> None:
        await self._calls.done(due, through=through)

    async def failed(self, due: CallDue, error: str) -> int:
        return await self._calls.failed(due, error)
