"""Conversation lifecycle for a chat session: open and replay, a reply settled into
history, and reset.

Module-level functions that take the session, as `voice/realtime/workers.py` does for
`RealtimeSession`. `session.py` holds the turn loop; this is everything around a turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.kernel.taint import Source
from app.services.tools import claims, relayed

if TYPE_CHECKING:
    from app.services.chat.session import ChatSession
    from app.services.chat.store import StoredMessage


async def open_conversation(s: ChatSession, conversation_id: object) -> None:
    """Attach to a stored conversation and replay it, or start a fresh one."""
    await s._cancel_turn()
    if conversation_id is None:
        s._conversation_id = None
        reset_history(s, [])
        await s._send({"type": "opened", "conversation_id": None, "messages": []})
        return
    if not isinstance(conversation_id, int) or isinstance(conversation_id, bool):
        await _open_failed(s, "I couldn't find that conversation.")
        return
    wanted = conversation_id
    conversation = await s._store.get(wanted)
    if conversation is None:
        await _open_failed(s, "That conversation is no longer here.")
        return
    stored = await s._store.messages(wanted)
    s._conversation_id = wanted
    # A conversation belongs to its own project, whatever the surface currently has
    # selected — otherwise opening it from "Everything" would silently widen scope.
    s._project_id = conversation.project_id
    reset_history(s, *replayed(stored))
    await s._send(
        {
            "type": "opened",
            "conversation_id": wanted,
            "messages": [m.model_dump(mode="json") for m in stored],
        }
    )


async def _open_failed(s: ChatSession, message: str) -> None:
    """Leave the previous conversation as well as saying so.

    The surface now shows a new chat. Staying attached would file the next message
    into the conversation that was on screen before, with nothing to show for it.
    """
    s._conversation_id = None
    reset_history(s, [])
    await s._fail("bad_conversation", message)


def replayed(stored: list[StoredMessage]) -> tuple[list[dict[str, Any]], dict[int, Source]]:
    """The model's history from stored rows, and which of its replies carried what she
    read: a cited one, or one after a tool row.

    Only user/assistant turns rejoin the history: tool rows are a record of what
    happened, and replaying them without their call ids confuses the model.
    """
    history: list[dict[str, Any]] = []
    read: dict[int, Source] = {}
    after_tool = False
    for m in stored:
        if m.role == "tool":
            after_tool = True
        elif m.role in ("user", "assistant") and m.content:
            if m.role == "assistant" and (m.citations or after_tool):
                read[len(history)] = "document" if m.citations else "tool_result"
            content = m.content
            if m.role == "assistant":  # as the user saw it, what it didn't do under it
                content = claims.with_stored(content, m.caveats)
            history.append({"role": m.role, "content": content})
            after_tool = False
    return history, read


def settle(s: ChatSession, reply: str, read: list[tuple[Source, str]]) -> list[dict[str, str]]:
    """Put her reply in the model's history as the user sees it, with what it said she
    did and she didn't (bug 37), and what it passed on from a line written to her in
    what she read (bug 20), after it; and return those caveats to store and show."""
    apps = s._settings.capabilities.app_allowlist
    found = claims.check(reply, s._ran, apps, surface="chat")
    texts = [*(text for _source, text in read), *(outcome.text for outcome in s._ran)]
    kept = [*claims.caveats(found), *relayed.caveats(relayed.check(reply, texts, surface="chat"))]
    s._history.append({"role": "assistant", "content": claims.with_stored(reply, kept)})
    return kept


def reset_history(
    s: ChatSession, history: list[dict[str, Any]], read: dict[int, Source] | None = None
) -> None:
    """Swap the history and the index that points into it, together: `_read` kept
    from the conversation before would mark the new one's replies as quoting what
    she read."""
    s._history = history
    s._read = read or {}


def carried(s: ChatSession, messages: list[dict[str, Any]]) -> list[tuple[Source, str]]:
    """Her replies in this prompt's window that carried what she read: untrusted, like
    the text they came from, or a sentence she repeated one turn would be nobody's the
    next (`kernel/taint.py`)."""
    window = {id(message) for message in messages}
    return [
        (source, s._history[index]["content"])
        for index, source in s._read.items()
        if id(s._history[index]) in window
    ]
