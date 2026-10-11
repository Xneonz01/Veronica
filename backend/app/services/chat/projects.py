"""Projects: a named workspace holding its own conversations and documents.

Projects are a table: a conversation carries its project's id, and nothing in memory
is scoped by project.

Scoping is applied at retrieval: a conversation belongs to a project, and inside a
project the model only retrieves from documents attached to that project's
conversations. Outside any project, retrieval covers every document.
"""

from __future__ import annotations

from datetime import datetime

import aiosqlite
from pydantic import BaseModel

from app.services.memory import cipher
from app.services.memory.models import _now
from app.services.memory.store import MemoryStore

_PROJECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

# Added to an existing table, so it is an ALTER. NULL means "not in a project", which
# is where every existing conversation lands.
_PROJECT_COLUMN = "ALTER TABLE chat_conversations ADD COLUMN project_id INTEGER"

_NAME_CHARS = 60

# `ProjectStore` defines a method called `list`, which shadows the builtin inside the
# class body (as in ChatStore). Aliases keep the annotations meaning the generic.
DocumentIds = list[int]


class Project(BaseModel):
    id: int
    name: str
    created_at: datetime
    conversation_count: int = 0


Projects = list[Project]


def _to_project(row: aiosqlite.Row) -> Project:
    keys = row.keys()
    return Project(
        id=row["id"],
        name=row["name"],
        created_at=datetime.fromisoformat(row["created_at"]),
        conversation_count=row["conversation_count"] if "conversation_count" in keys else 0,
    )


class ProjectStore:
    """Projects, and which conversation belongs to which."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    @property
    def _db(self) -> aiosqlite.Connection:
        return self._memory.db

    async def init_schema(self) -> None:
        async with self._memory.transaction() as db:
            await db.executescript(_PROJECT_SCHEMA)
            try:
                await db.execute(_PROJECT_COLUMN)
            except cipher.OPERATIONAL_ERRORS:
                pass  # already migrated; SQLite has no ADD COLUMN IF NOT EXISTS

    async def create(self, name: str) -> Project:
        clean = " ".join(name.split())[:_NAME_CHARS] or "Untitled project"
        now = _now().isoformat()
        async with self._memory.transaction() as db:
            cur = await db.execute(
                "INSERT INTO chat_projects (name, created_at) VALUES (?,?)", (clean, now)
            )
        return Project(
            id=int(cur.lastrowid or 0), name=clean, created_at=datetime.fromisoformat(now)
        )

    async def list(self) -> Projects:
        async with self._db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM chat_conversations c "
            "WHERE c.project_id = p.id) AS conversation_count "
            "FROM chat_projects p ORDER BY p.name COLLATE NOCASE"
        ) as cur:
            rows = await cur.fetchall()
        return [_to_project(row) for row in rows]

    async def get(self, project_id: int) -> Project | None:
        async with self._db.execute(
            "SELECT * FROM chat_projects WHERE id = ?", (project_id,)
        ) as cur:
            row = await cur.fetchone()
        return _to_project(row) if row else None

    async def rename(self, project_id: int, name: str) -> bool:
        clean = " ".join(name.split())[:_NAME_CHARS] or "Untitled project"
        async with self._memory.transaction() as db:
            cur = await db.execute(
                "UPDATE chat_projects SET name = ? WHERE id = ?", (clean, project_id)
            )
        return cur.rowcount > 0

    async def delete(self, project_id: int) -> bool:
        """Remove the project. Its conversations survive, unfiled.

        Deleting a folder shouldn't delete the work inside it; the user can still
        delete conversations one by one.
        """
        async with self._memory.transaction() as db:
            await db.execute(
                "UPDATE chat_conversations SET project_id = NULL WHERE project_id = ?",
                (project_id,),
            )
            cur = await db.execute("DELETE FROM chat_projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0

    async def assign(self, conversation_id: int, project_id: int | None) -> bool:
        async with self._memory.transaction() as db:
            cur = await db.execute(
                "UPDATE chat_conversations SET project_id = ? WHERE id = ?",
                (project_id, conversation_id),
            )
        return cur.rowcount > 0

    async def document_ids_for(self, project_id: int) -> DocumentIds:
        """Documents attached to this project's conversations — the retrieval scope."""
        async with self._db.execute(
            "SELECT DISTINCT a.document_id FROM chat_attachments a "
            "JOIN chat_conversations c ON c.id = a.conversation_id "
            "WHERE c.project_id = ? AND a.document_id IS NOT NULL",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [int(row["document_id"]) for row in rows]
