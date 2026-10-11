"""The stores in memory.db, opened together at start-up, and what it means when they
can't be.

Every store on the file makes its tables, then the file is upgraded, in this one place,
because the order matters: the upgrade waits for every store's tables
(`services/memory/migrations.py`). What opening raises is judged by the start
(`memory_problem`): a kind the person can fix lets the backend go on without these
stores (`core/problems.py`), anything else still stops it. Whatever was opened is closed
before the error goes on.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.embeddings.e5_embedder import EMBED_MODEL_ID
from app.capabilities import register_capabilities
from app.core.config import Settings
from app.core.data_key import Locked, key_path, open_key
from app.core.event_bus import EventBus
from app.core.file_lock import held, lock_path
from app.core.gpu_manager import CudaGpuModelManager
from app.core.problems import Problem
from app.kernel.runtime import KernelRuntime, build_kernel_runtime
from app.left_behind import MemoryLeftBehind, memory_left_behind
from app.paths import anchored
from app.services.chat.attachments import AttachmentStore
from app.services.chat.projects import ProjectStore
from app.services.chat.store import ChatStore
from app.services.connected.runtime import ConnectedRuntime, build_connected
from app.services.memory import at_rest, cipher, migrations, upgrade_copy
from app.services.memory.migrations import DatabaseTooNewError
from app.services.memory.runtime import MemoryRuntime, build_memory_runtime
from app.services.memory.upgrade_copy import CopyError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Stores:
    """Everything that lives in memory.db, open and at this build's version, and what
    opening it found that didn't stop it (a file not encrypted yet)."""

    memory: MemoryRuntime
    kernel: KernelRuntime
    chat: ChatStore
    attachments: AttachmentStore
    projects: ProjectStore
    web: ConnectedRuntime
    problems: list[Problem] = field(default_factory=list)


def unlock(path: Path) -> tuple[bytes | None, list[Problem]]:
    """The data key to her memory file at `path` (`core/data_key.py`), kept beside it and
    made on the first start that has none and nothing encrypted to lose, and the file
    encrypted with it if still plain (`at_rest.py`); what that found to say. Under the
    key's lock (`core/file_lock.py`): two starts at once would each make a key, and one
    encrypt with the key the other set aside. `Locked` when what is encrypted can't be
    opened on this account, or with this key. No key where the system has none to keep
    (not Windows): the file stays plain, and that is said. Blocking."""
    key_file = key_path(path)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    with held(lock_path(key_file)):
        key = open_key(key_file, encrypted=at_rest.encrypted_anything(path))
        if key is None:
            return None, [Problem(code="memory_not_encrypted", detail={"reason": "unsupported"})]
        problems: list[Problem] = []
        try:
            at_rest.encrypt(path, key)
        except at_rest.NotEncrypted as error:
            logger.warning("her memory, or a copy of it, stays plain for now (%s)", error)
            problems.append(_not_encrypted(error))
        at_rest.check_key(path, key)
        return key, problems


async def open_stores(
    settings: Settings, bus: EventBus, manager: CudaGpuModelManager, data_dir: Path
) -> Stores:
    """Build and open every store on memory.db, then upgrade the file. Not when that
    would start a second, empty memory beside hers (`left_behind.py`): checked before
    anything is made where it is kept, its key included. A file still plain is
    encrypted with the data key first (`unlock`); one that can't be yet is opened as it
    is, and said, and the next start tries again."""
    if memory_left_behind(settings):
        raise MemoryLeftBehind("still in the installation's data folder, none where it's kept now")
    path = anchored(settings.memory.db_path)
    key, problems = await asyncio.to_thread(unlock, path)
    opened_with = key if not await asyncio.to_thread(_plain, path) else None
    memory = build_memory_runtime(settings, bus, manager, opened_with)
    # The kernel's audit store borrows the memory store's connection.
    kernel = build_kernel_runtime(settings, bus, memory.store, policy_path=data_dir / "policy.json")
    # The web's settings and its one client; its key sealed with the data key.
    web = build_connected(settings.capabilities, bus, data_dir, key)
    register_capabilities(kernel.registry, settings, memory=memory, web=web)
    try:
        await memory.init()
        await kernel.init()
        chat = ChatStore(memory.store)
        await chat.init_schema()
        attachments = AttachmentStore(memory.store, data_dir / "attachments")
        await attachments.init_schema()
        # After ChatStore: ProjectStore adds a column to chat_conversations.
        projects = ProjectStore(memory.store)
        await projects.init_schema()
        await migrations.upgrade(memory.store)
    except BaseException:
        # Nothing on the file will be served, so a call's mode switch must not load
        # its model for nothing.
        manager.unregister(EMBED_MODEL_ID)
        with suppress(Exception):
            await kernel.aclose()
        with suppress(Exception):
            await web.aclose()
        with suppress(Exception):
            await memory.store.close()
        raise
    # Only now, the file open and whole: a start that finds it damaged or too new keeps
    # every copy, since one of them is the way back. What can't go now goes next time.
    with suppress(OSError):
        upgrade_copy.remove_old(anchored(settings.memory.db_path))
    return Stores(memory, kernel, chat, attachments, projects, web, problems)


def memory_problem(error: Exception) -> Problem | None:
    """What an error opening or upgrading memory.db means for the person; None for one of
    no known kind, which still stops the start."""
    if isinstance(error, MemoryLeftBehind):
        return Problem(code="memory_left_behind")
    if isinstance(error, Locked):
        return Problem(code="memory_locked", detail=_locked(error))
    if isinstance(error, DatabaseTooNewError):
        copy = error.copy.name if error.copy else ""
        return Problem(code="memory_too_new", detail={"copy": copy})
    if isinstance(error, CopyError):  # before OSError, its parent
        if error.damaged:
            return Problem(code="memory_damaged")
        if error.needed_mb is None:
            return Problem(code="memory_copy_failed")
        room = {"needed_mb": error.needed_mb, "free_mb": error.free_mb or 0}
        return Problem(code="memory_no_room", detail=room)
    if isinstance(error, cipher.SQLITE_ERRORS):
        return _sqlite_problem(error)
    if isinstance(error, OSError):  # the folder closed to her, a file where it should be
        return Problem(code="memory_unopenable")
    return None


# SQLite's primary result codes. The exception's class doesn't tell a file that won't
# open from a statement of ours that is wrong: both are OperationalError, and the second
# is SQLITE_ERROR, a bug, which must stop the start rather than read as a held file. So
# is SQLITE_LOCKED, a clash within one connection: ours.
_DAMAGED = frozenset({sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB})
_UNOPENABLE = frozenset(
    {
        sqlite3.SQLITE_CANTOPEN,
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_READONLY,
        sqlite3.SQLITE_PERM,
        sqlite3.SQLITE_IOERR,
    }
)


def _sqlite_problem(error: Exception) -> Problem | None:
    code = getattr(error, "sqlite_errorcode", None)
    if code is None:  # raised by the sqlite3 module, not SQLite: a misuse of ours
        return None
    primary = code & 0xFF  # an extended code (SQLITE_CANTOPEN_ISDIR) keeps it here
    if primary in _DAMAGED:
        return Problem(code="memory_damaged")
    if primary in _UNOPENABLE:
        return Problem(code="memory_unopenable")
    if primary == sqlite3.SQLITE_FULL:
        return Problem(code="memory_no_room")
    return None


def _locked(error: Locked) -> dict[str, str | int]:
    """A lock's reason, and whether a recovery key can open it."""
    return {"reason": error.reason} | ({"recoverable": 1} if error.recoverable else {})


def _not_encrypted(error: at_rest.NotEncrypted) -> Problem:
    detail: dict[str, str | int] = {} if error.copy is None else {"copy": error.copy}
    if error.needed_mb is not None:
        detail |= {"needed_mb": error.needed_mb, "free_mb": error.free_mb or 0}
    return Problem(code="memory_not_encrypted", detail=detail)


def _plain(path: Path) -> bool:
    """Whether her memory file is there and still plain: then it is opened without a key."""
    try:
        return path.stat().st_size > 0 and cipher.plain(path)
    except FileNotFoundError:
        return False
