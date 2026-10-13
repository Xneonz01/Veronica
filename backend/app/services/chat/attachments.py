"""Files the user attaches to a conversation.

Two kinds, handled differently:

* documents (.txt/.md/.pdf and plain-text code) go through the RAG pipeline, so the
  model reads them and answers can cite them;
* images are stored and listed, nothing more. No vision model is available, so the
  record says `understood=False` and the UI states that the image wasn't read rather
  than implying it was.

Files live under `attachments/` in her data folder (`data_dir`), named by id, so a
user-supplied filename never becomes part of a path. They are sealed with her data key
(`core/sealed.py`, AES-256-GCM bound to the file's own name) where there is one, as her
voice clips are, and a plain one from before is sealed in place at start-up: a photo of
a document is as private as the document. Without a data key (not Windows) they stay
plain, and nothing reads them back that would need the key.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from datetime import datetime
from pathlib import Path

import aiosqlite
from pydantic import BaseModel

from app.core.sealed import is_sealed, seal, seal_in_place
from app.services.memory.models import _now
from app.services.memory.rag import CODE_SUFFIXES
from app.services.memory.store import MemoryStore

logger = logging.getLogger(__name__)
_SEALING = ".sealing"  # a sealed copy being written beside its plain file

_ATTACHMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  document_id INTEGER,
  stored_path TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_conv
  ON chat_attachments(conversation_id, id);
"""

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})


class Attachment(BaseModel):
    id: int
    conversation_id: int
    kind: str  # document | image
    name: str
    mime: str
    size_bytes: int
    document_id: int | None = None
    created_at: datetime
    # False for images. Sent to the UI so it can state the limitation instead of
    # leaving the user to assume the image was looked at.
    understood: bool = False


Attachments = list[Attachment]
# `AttachmentStore` has no `list` method, but it uses module-level aliases like the
# neighbouring stores, so adding one later can't shadow the builtin in its annotations.
DocumentIds = list[int]


def classify(filename: str) -> str:
    """document | image | unsupported — from the suffix alone, before anything is read."""
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in CODE_SUFFIXES or suffix in {".txt", ".text", ".md", ".markdown", ".pdf"}:
        return "document"
    return "unsupported"


def _to_attachment(row: aiosqlite.Row) -> Attachment:
    return Attachment(
        id=row["id"],
        conversation_id=row["conversation_id"],
        kind=row["kind"],
        name=row["name"],
        mime=row["mime"],
        size_bytes=row["size_bytes"],
        document_id=row["document_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        understood=row["kind"] == "document",
    )


class AttachmentStore:
    """Attachment records on the shared memory connection; bytes on disk."""

    def __init__(self, memory: MemoryStore, root: Path, key: bytes | None = None) -> None:
        self._memory = memory
        self._root = root
        self._key = key

    @property
    def _db(self) -> aiosqlite.Connection:
        return self._memory.db

    async def init_schema(self) -> None:
        async with self._memory.transaction() as db:
            await db.executescript(_ATTACHMENT_SCHEMA)
        if self._key is not None:
            await asyncio.to_thread(self._seal_existing, self._key)

    def _seal_existing(self, key: bytes) -> None:
        """Seal what an earlier version left plain; what can't be now is tried next start."""
        if not self._root.is_dir():
            return
        for file in self._root.iterdir():
            if not file.is_file() or file.name.endswith(_SEALING):
                continue
            try:
                if not is_sealed(file):
                    seal_in_place(file, key, marker=_SEALING)
            except OSError as error:
                logger.warning("an attachment stays plain for now (%s)", type(error).__name__)

    def path_for(self, attachment_id: int, name: str) -> Path:
        """`<id><suffix>` — the user's filename never becomes part of the path."""
        return self._root / f"{attachment_id}{Path(name).suffix.lower()}"

    def _files(self, attachment_id: int, name: str, stored: Path | None) -> set[Path]:
        """Where an attachment's bytes can be: where they were written, and where they
        would be now. Moving `data_dir` leaves one of the two behind, depending on
        whether the files were moved with it. A stored path is only trusted when it
        names this attachment's own file in an attachments folder."""
        current = self.path_for(attachment_id, name)
        if stored is not None and (stored.name, stored.parent.name) == (
            current.name,
            "attachments",
        ):
            return {current, stored}
        return {current}

    async def add(
        self,
        conversation_id: int,
        *,
        kind: str,
        name: str,
        size_bytes: int,
        document_id: int | None = None,
        data: bytes | None = None,
    ) -> Attachment:
        mime = mimetypes.guess_type(name)[0] or ""
        async with self._memory.transaction() as db:
            cur = await db.execute(
                "INSERT INTO chat_attachments (conversation_id, kind, name, mime, size_bytes, "
                "document_id, stored_path, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    conversation_id,
                    kind,
                    name,
                    mime,
                    size_bytes,
                    document_id,
                    None,
                    _now().isoformat(),
                ),
            )
            attachment_id = int(cur.lastrowid or 0)
            stored: Path | None = None
            if data is not None:
                self._root.mkdir(parents=True, exist_ok=True)
                stored = self.path_for(attachment_id, name)
                kept = data if self._key is None else seal(data, self._key, stored.name)
                stored.write_bytes(kept)
                await db.execute(
                    "UPDATE chat_attachments SET stored_path = ? WHERE id = ?",
                    (str(stored), attachment_id),
                )
        return Attachment(
            id=attachment_id,
            conversation_id=conversation_id,
            kind=kind,
            name=name,
            mime=mime,
            size_bytes=size_bytes,
            document_id=document_id,
            created_at=_now(),
            understood=kind == "document",
        )

    async def list_for(self, conversation_id: int) -> Attachments:
        async with self._db.execute(
            "SELECT * FROM chat_attachments WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_attachment(row) for row in rows]

    async def get(self, attachment_id: int) -> tuple[Attachment, Path | None] | None:
        async with self._db.execute(
            "SELECT * FROM chat_attachments WHERE id = ?", (attachment_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        stored = Path(row["stored_path"]) if row["stored_path"] else None
        return _to_attachment(row), stored

    async def remove(self, attachment_id: int) -> tuple[bool, int | None]:
        """Drop the record and its bytes. Returns (removed, document_id to unindex)."""
        found = await self.get(attachment_id)
        if found is None:
            return False, None
        attachment, stored = found
        async with self._memory.transaction() as db:
            await db.execute("DELETE FROM chat_attachments WHERE id = ?", (attachment_id,))
        _delete(self._files(attachment_id, attachment.name, stored))
        return True, attachment.document_id

    async def purge_for_conversation(self, conversation_id: int) -> DocumentIds:
        """Drop every attachment of a conversation. Returns documents to unindex.

        Without this, deleting a conversation would leave its rows and files behind,
        and its indexed documents would stay retrievable after the conversation was gone.
        """
        async with self._db.execute(
            "SELECT id, name, stored_path, document_id FROM chat_attachments "
            "WHERE conversation_id = ?",
            (conversation_id,),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return []
        documents = [row["document_id"] for row in rows if row["document_id"] is not None]
        async with self._memory.transaction() as db:
            await db.execute(
                "DELETE FROM chat_attachments WHERE conversation_id = ?", (conversation_id,)
            )
        for row in rows:
            stored = Path(row["stored_path"]) if row["stored_path"] else None
            _delete(self._files(row["id"], row["name"], stored))
        return documents


def _delete(paths: set[Path]) -> None:
    """Delete an attachment's files. One held open by another program stays behind and is
    logged, without its path: it must not stop the rest, or keep its document indexed
    after its conversation is gone."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not delete an attachment file (%s)", type(exc).__name__)
