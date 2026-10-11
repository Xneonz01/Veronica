"""Conversation persistence, so chats survive a restart.

Same shape as `RagStore`: its own tables and `init_schema()`, borrowing the memory
store's single connection so every write takes the one write lock instead of
committing underneath another coroutine.

Messages are stored as they are displayed, not as the model saw them. The system
prompt, the RAG block and the tool scaffolding are rebuilt each turn from live state
(personality and memory change between turns), so storing them would replay a stale
prompt on reload.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aiosqlite
from pydantic import BaseModel

from app.services.chat import learning_queue
from app.services.memory.models import _now
from app.services.memory.schema import ensure_column
from app.services.memory.store import MemoryStore

_CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- No ON DELETE CASCADE: this connection never sets `PRAGMA foreign_keys = ON`, so a
-- cascade clause would read as a guarantee and do nothing. Deletes are explicit and
-- in one transaction, the same way RagStore removes a document's chunks.
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  tool_name TEXT,
  tool_ok INTEGER,
  citations_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  caveats_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
  ON chat_messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_chat_conversations_updated
  ON chat_conversations(updated_at DESC);
"""

# What a fresh conversation is called until the first message names it.
UNTITLED = "New conversation"
_TITLE_CHARS = 60

# Module-level aliases: `ChatStore` defines a method called `list`, which shadows the
# builtin inside the class body, so `list[...]` in a signature there resolves to the
# method instead of the generic.
Citations = list[dict[str, object]]
# What the claim check found an answer said without doing it (`tools/claims.py`): kept
# beside the answer, not in it; the model's history puts it back after (`lifecycle.py`).
Caveats = list[dict[str, str]]


class Conversation(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    # Added by ProjectStore's migration, so a row read before it ran has no column.
    project_id: int | None = None


class StoredMessage(BaseModel):
    id: int
    role: str  # user | assistant | tool
    content: str
    tool_name: str | None = None
    tool_ok: bool | None = None
    citations: Citations = []
    caveats: Caveats = []
    created_at: datetime


Conversations = list[Conversation]
Messages = list[StoredMessage]


def derive_title(first_user_message: str) -> str:
    """Title a conversation from its opening line.

    Asking the model for a title would put a second generation in front of the first
    reply, the most latency-visible moment in the surface.
    """
    flat = " ".join(first_user_message.split())
    if not flat:
        return UNTITLED
    if len(flat) <= _TITLE_CHARS:
        return flat
    return flat[:_TITLE_CHARS].rsplit(" ", 1)[0] + "…"


def _to_conversation(row: aiosqlite.Row) -> Conversation:
    keys = row.keys()
    return Conversation(
        id=row["id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        message_count=row["message_count"] if "message_count" in keys else 0,
        project_id=row["project_id"] if "project_id" in keys else None,
    )


class ChatStore:
    """Conversations and their messages, on the shared memory connection."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    @property
    def _db(self) -> aiosqlite.Connection:
        return self._memory.db

    async def init_schema(self) -> None:
        async with self._memory.transaction() as db:
            await db.executescript(_CHAT_SCHEMA + learning_queue.SCHEMA)
            # Schema 2 (0.19.0): a file from before has the table without it.
            await ensure_column(db, "chat_messages", "caveats_json", "TEXT NOT NULL DEFAULT '[]'")
            await ensure_column(db, "chat_learning", "last_error", "TEXT")

    async def create(self, title: str = UNTITLED) -> Conversation:
        now = _now().isoformat()
        async with self._memory.transaction() as db:
            cur = await db.execute(
                "INSERT INTO chat_conversations (title, created_at, updated_at) VALUES (?,?,?)",
                (title, now, now),
            )
        return Conversation(
            id=int(cur.lastrowid or 0),
            title=title,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    async def get(self, conversation_id: int) -> Conversation | None:
        async with self._db.execute(
            "SELECT * FROM chat_conversations WHERE id = ?", (conversation_id,)
        ) as cur:
            row = await cur.fetchone()
        return _to_conversation(row) if row else None

    async def list(self, *, limit: int = 100) -> Conversations:
        async with self._db.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM chat_messages m "
            "WHERE m.conversation_id = c.id) AS message_count "
            "FROM chat_conversations c ORDER BY c.updated_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_conversation(row) for row in rows]

    async def rename(self, conversation_id: int, title: str) -> bool:
        clean = " ".join(title.split())[:_TITLE_CHARS] or UNTITLED
        async with self._memory.transaction() as db:
            cur = await db.execute(
                "UPDATE chat_conversations SET title = ?, updated_at = ? WHERE id = ?",
                (clean, _now().isoformat(), conversation_id),
            )
        return cur.rowcount > 0

    async def delete(self, conversation_id: int) -> bool:
        # One transaction: messages orphaned by a half-done delete would never be
        # reachable again, but would still be counted by the listing query.
        async with self._memory.transaction() as db:
            await db.execute(
                "DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,)
            )
            await learning_queue.forget(db, conversation_id)
            cur = await db.execute(
                "DELETE FROM chat_conversations WHERE id = ?", (conversation_id,)
            )
        return cur.rowcount > 0

    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        *,
        tool_name: str | None = None,
        tool_ok: bool | None = None,
        citations: Citations | None = None,
        caveats: Caveats | None = None,
    ) -> int:
        now = _now().isoformat()
        async with self._memory.transaction() as db:
            cur = await db.execute(
                "INSERT INTO chat_messages (conversation_id, role, content, tool_name, "
                "tool_ok, citations_json, created_at, caveats_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    conversation_id,
                    role,
                    content,
                    tool_name,
                    None if tool_ok is None else int(tool_ok),
                    json.dumps(citations or []),
                    now,
                    json.dumps(caveats or []),
                ),
            )
            # Bumped in the same transaction: the sidebar orders by this, so a message
            # that landed without it would leave the conversation sorted as untouched.
            await db.execute(
                "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            message_id = int(cur.lastrowid or 0)
            # Here too, so no message is ever stored without learning knowing of it.
            await learning_queue.mark(db, conversation_id, message_id)
        return message_id

    async def messages(self, conversation_id: int, *, limit: int = 500) -> Messages:
        """The most recent `limit` messages, oldest-first.

        Take the tail, then restore chronological order. A plain `ORDER BY id LIMIT ?`
        would return the oldest rows, so a long conversation would reopen at its
        beginning and the session would rebuild its context from stale turns.
        """
        async with self._db.execute(
            "SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_message(row) for row in reversed(list(rows))]

    async def messages_through(
        self, conversation_id: int, through_id: int, *, limit: int
    ) -> Messages:
        """The last `limit` messages up to `through_id`, oldest first: what learning has
        read, whose last turns the next part is sent with (`learning_sources.py`). Rows
        that old are committed, so no lock is needed to read them."""
        async with self._db.execute(
            "SELECT * FROM chat_messages WHERE conversation_id = ? AND id <= ? "
            "ORDER BY id DESC LIMIT ?",
            (conversation_id, through_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_message(row) for row in reversed(list(rows))]

    async def messages_after(self, conversation_id: int, after_id: int, *, limit: int) -> Messages:
        """Up to `limit` messages newer than `after_id`, oldest first: what learning
        hasn't read yet (`learning_queue.py`).

        Read under the write lock: the connection is shared, so a read beside another
        coroutine's open transaction sees its uncommitted message, and a rollback would
        then hand that message's id to the next one, which learning would never read.
        """
        async with (
            self._memory.transaction() as db,
            db.execute(
                "SELECT * FROM chat_messages WHERE conversation_id = ? AND id > ? "
                "ORDER BY id LIMIT ?",
                (conversation_id, after_id, limit),
            ) as cur,
        ):
            rows = await cur.fetchall()
        return [_to_message(row) for row in rows]


def _json_list(text: str) -> list[Any]:
    """A stored JSON list, or [] for anything else: a damaged row still opens."""
    try:
        value = json.loads(text)
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _is_caveat(item: object) -> bool:
    """A stored caveat of the shape the model expects, or the row would fail to open."""
    return isinstance(item, dict) and all(isinstance(value, str) for value in item.values())


def _to_message(row: aiosqlite.Row) -> StoredMessage:
    raw = row["tool_ok"]
    return StoredMessage(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        tool_name=row["tool_name"],
        tool_ok=None if raw is None else bool(raw),
        citations=[item for item in _json_list(row["citations_json"]) if isinstance(item, dict)],
        caveats=[item for item in _json_list(row["caveats_json"]) if _is_caveat(item)],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
