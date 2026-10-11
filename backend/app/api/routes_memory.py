"""Memory REST: browse and edit memories, review candidates, set the learning mode.

Backs the Memory page through the MemoryService and LearningController on
``app.state.memory_runtime``. Memory changes publish ``memory.updated`` (a mode change
publishes ``memory.learning_mode``), so connected surfaces refresh live. Errors carry
a safe ``detail``; the sensitivity guard surfaces as a 422 and a credential is never
written.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from app.api.ids import RowId
from app.services.memory.models import (
    MAX_MEMORY_CHARS,
    LearningMode,
    Memory,
    MemoryCreate,
    MemoryKind,
)
from app.services.memory.runtime import MemoryRuntime
from app.services.memory.sensitive import SensitiveContentError

router = APIRouter(prefix="/memory", tags=["memory"])

_EVERY = -1  # SQLite's LIMIT -1: no limit
# The review queue shows the newest this many; older candidates follow as it empties.
_CANDIDATES_SHOWN = 200


def _runtime(request: Request) -> MemoryRuntime:
    runtime: MemoryRuntime | None = getattr(request.app.state, "memory_runtime", None)
    if runtime is None:  # started without her memory (`app/stores.py`)
        raise HTTPException(status_code=503, detail="Memory is unavailable.")
    return runtime


class MemoryEdit(BaseModel):
    content: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    pinned: bool | None = None


class LearningModeBody(BaseModel):
    mode: LearningMode


class MemoryNote(BaseModel):
    """A thought the user typed and asked to keep."""

    # Strip before the length check so whitespace-only input is rejected.
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MEMORY_CHARS)
    ]
    kind: MemoryKind = MemoryKind.fact
    pinned: bool = False


@router.post("", status_code=201)
async def create_memory(body: MemoryNote, request: Request) -> Memory:
    """Save something the user explicitly asked to keep (quick capture).

    `MemoryService.add_if_new` skips a note that is already stored and otherwise goes
    through the same `add` as the `memory_save` tool, so it publishes the same
    `memory.updated` event and gets the same on-screen receipt with Undo. Going through
    the chat tool instead would make the save depend on the model choosing to call it.

    `source="user"` marks it as explicit rather than inferred on the Memory page.
    """
    try:
        memory = await _runtime(request).service.add_if_new(
            MemoryCreate(
                kind=body.kind,
                content=body.content,
                pinned=body.pinned,
                source="user",
            )
        )
    except SensitiveContentError as exc:
        # The sensitivity guard applies here as everywhere else: a credential is
        # never stored, however it arrived.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if memory is None:
        # Already known. Not an error, but the caller must be told so the UI doesn't
        # report a write that never happened.
        raise HTTPException(status_code=409, detail="I already have that one.")
    return memory


@router.get("")
async def list_memories(
    request: Request,
    kind: MemoryKind | None = None,
    pending: bool | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    """Every memory she keeps, which her cap bounds (`max_active_memories`; her profile
    and what she was asked to pin aside), then the newest candidates waiting for review.
    The 200 newest of all of them left an older profile slot or pinned memory off the
    Memory page, and out of its search (bug 11)."""
    service = _runtime(request).service
    memories: list[Memory] = []
    if pending is not True:
        memories += await service.list(kind=kind, pending=False, limit=_EVERY)
    if pending is not False:
        memories += await service.list(kind=kind, pending=True, limit=_CANDIDATES_SHOWN)
    if q and q.strip():
        needle = q.strip().lower()
        memories = [m for m in memories if needle in m.content.lower()]
    return {"memories": [m.model_dump(mode="json") for m in memories]}


@router.get("/learning-mode")
async def get_learning_mode(request: Request) -> dict[str, str]:
    return {"mode": _runtime(request).learning.mode.value}


@router.put("/learning-mode")
async def set_learning_mode(body: LearningModeBody, request: Request) -> dict[str, str]:
    await _runtime(request).learning.set_mode(body.mode)
    return {"mode": body.mode.value}


@router.patch("/{memory_id}")
async def edit_memory(memory_id: RowId, body: MemoryEdit, request: Request) -> Memory:
    try:
        memory = await _runtime(request).service.update(
            memory_id,
            content=body.content,
            importance=body.importance,
            pinned=body.pinned,
            origin="ui",
        )
    except SensitiveContentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if memory is None:
        raise HTTPException(status_code=404, detail="That memory no longer exists.")
    return memory


@router.delete("/{memory_id}")
async def delete_memory(memory_id: RowId, request: Request) -> dict[str, int]:
    await _runtime(request).service.delete(memory_id, origin="ui")
    return {"deleted": memory_id}


@router.post("/{memory_id}/approve")
async def approve_candidate(memory_id: RowId, request: Request) -> Memory:
    memory = await _runtime(request).service.approve(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="That candidate no longer exists.")
    return memory


@router.post("/{memory_id}/reject")
async def reject_candidate(memory_id: RowId, request: Request) -> dict[str, int]:
    # Only a candidate: a memory she keeps is deleted with Delete, which offers Undo.
    if await _runtime(request).service.reject(memory_id) is None:
        raise HTTPException(status_code=404, detail="That candidate no longer exists.")
    return {"rejected": memory_id}


@router.post("/{memory_id}/undo")
async def undo_memory(memory_id: RowId, request: Request) -> dict[str, int | None]:
    # A supersede rolls back to the original; a plain add is archived. Used by the toast.
    restored = await _runtime(request).service.undo(memory_id)
    return {"undone": memory_id, "restored": restored.id if restored is not None else None}
