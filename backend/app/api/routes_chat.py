"""Conversation REST: conversations and their attachments.

The list returns conversations without their messages; those come with a single
conversation (or over `/ws/chat` when it is opened), so opening the app doesn't
pull every message ever sent.
"""

from __future__ import annotations

import logging
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.ids import RowId
from app.api.refusals import Refusal
from app.api.uploads import read_or_refuse
from app.services.chat.attachments import AttachmentStore, classify
from app.services.chat.store import ChatStore
from app.services.memory.rag import RagError, RagService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["chat"])


def _store(request: Request) -> ChatStore:
    store = getattr(request.app.state, "chat_store", None)
    if store is None:  # started without her memory (`app/stores.py`)
        raise HTTPException(status_code=503, detail="Conversations are unavailable.")
    chat_store: ChatStore = store
    return chat_store


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.get("")
async def list_conversations(
    request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> dict[str, Any]:
    rows = await _store(request).list(limit=limit)
    return {"conversations": [row.model_dump(mode="json") for row in rows]}


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: RowId, request: Request) -> dict[str, Any]:
    store = _store(request)
    conversation = await store.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="That conversation is no longer here.")
    messages = await store.messages(conversation_id)
    return {
        "conversation": conversation.model_dump(mode="json"),
        "messages": [m.model_dump(mode="json") for m in messages],
    }


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: RowId, body: Rename, request: Request
) -> dict[str, Any]:
    if not await _store(request).rename(conversation_id, body.title):
        raise HTTPException(status_code=404, detail="That conversation is no longer here.")
    return {"conversation_id": conversation_id, "title": body.title}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: RowId, request: Request) -> dict[str, Any]:
    """Delete the conversation with its attachments and their indexed documents.

    Attachments go first: if the conversation row were deleted first and the purge
    then failed, no surface could reach the attachments to delete them, and their
    documents would stay in retrieval.
    """
    if await _store(request).get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="That conversation is no longer here.")
    document_ids = await _attachments(request).purge_for_conversation(conversation_id)
    rag = _rag_service(request)
    for document_id in document_ids:
        with suppress(Exception):  # one bad document must not strand the rest
            await rag.delete(document_id)
    await _store(request).delete(conversation_id)
    return {"deleted": conversation_id, "documents_removed": len(document_ids)}


def _attachments(request: Request) -> AttachmentStore:
    store = getattr(request.app.state, "attachment_store", None)
    if store is None:  # started without her memory (`app/stores.py`)
        raise HTTPException(status_code=503, detail="Attachments are unavailable.")
    attachment_store: AttachmentStore = store
    return attachment_store


@router.get("/{conversation_id}/attachments")
async def list_attachments(conversation_id: RowId, request: Request) -> dict[str, Any]:
    rows = await _attachments(request).list_for(conversation_id)
    return {"attachments": [row.model_dump(mode="json") for row in rows]}


@router.post("/{conversation_id}/attachments")
async def add_attachment(
    conversation_id: RowId, request: Request, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    """Index a document, or store an image without claiming to have understood it."""
    store = _attachments(request)
    if await _store(request).get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="That conversation is no longer here.")

    name = Path(file.filename or "attachment").name
    kind = classify(name)
    if kind == "unsupported":
        raise Refusal(
            422, "unsupported_file", "I can read text, code and PDFs, and hold on to images."
        )
    data = await read_or_refuse(file)

    if kind == "image":
        # Images are stored and listed only. There is no vision model, so nothing
        # here should suggest the image was understood.
        attachment = await store.add(
            conversation_id, kind="image", name=name, size_bytes=len(data), data=data
        )
        return {"attachment": attachment.model_dump(mode="json")}

    rag = _rag_service(request)
    suffix = Path(name).suffix or ".txt"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(data)
        tmp = Path(handle.name)
    try:
        document = await rag.index_file(tmp, title=Path(name).stem)
    except RagError as exc:
        raise Refusal(422, exc.code, str(exc)) from exc
    except Exception as exc:  # a corrupt file must not surface as a parser trace
        logger.exception("attachment index failed")
        raise Refusal(422, "unreadable_file", "That file could not be read.") from exc
    finally:
        tmp.unlink(missing_ok=True)

    try:
        attachment = await store.add(
            conversation_id,
            kind="document",
            name=name,
            size_bytes=len(data),
            document_id=document.id,
        )
    except Exception:
        # The document is already indexed. Unindex it, or it would stay retrievable
        # (and citable) with no attachment row through which it could be removed.
        logger.exception("attachment record failed; unindexing the orphan document")
        with suppress(Exception):
            await rag.delete(document.id)
        raise Refusal(422, "attach_failed", "That file could not be attached.") from None
    return {
        "attachment": attachment.model_dump(mode="json"),
        "chunk_count": document.chunk_count,
    }


@router.delete("/{conversation_id}/attachments/{attachment_id}")
async def remove_attachment(
    conversation_id: RowId, attachment_id: RowId, request: Request
) -> dict[str, int]:
    store = _attachments(request)
    found = await store.get(attachment_id)
    # The id must belong to the conversation in the URL; otherwise any
    # conversation's endpoint could delete any attachment in the database.
    if found is None or found[0].conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="That attachment is already gone.")
    removed, document_id = await store.remove(attachment_id)
    if not removed:
        raise HTTPException(status_code=404, detail="That attachment is already gone.")
    if document_id is not None:
        # Also remove it from retrieval, or a deleted document keeps turning up in
        # citations.
        await _rag_service(request).delete(document_id)
    return {"deleted": attachment_id}


def _rag_service(request: Request) -> RagService:
    runtime = getattr(request.app.state, "memory_runtime", None)
    if runtime is None:  # started without her memory (`app/stores.py`)
        raise HTTPException(status_code=503, detail="Knowledge is unavailable.")
    rag: RagService = runtime.rag
    return rag
