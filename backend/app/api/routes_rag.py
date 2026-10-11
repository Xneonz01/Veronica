"""Documents REST: list, add and remove the RAG knowledge documents.

Backs the Knowledge panel on the Memory page. The RagService reads, chunks and embeds
each document; list-changing operations publish ``rag.updated`` so surfaces refresh
live. Unsupported, empty or unreadable files return a typed 422, never a parser trace.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app.api.ids import RowId
from app.api.refusals import Refusal
from app.api.uploads import read_body_capped, read_or_refuse
from app.services.memory.models import RagDocument
from app.services.memory.rag import MAX_TEXT_BYTES, TEXT_TOO_LONG, RagError, RagService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag/documents", tags=["rag"])


def _rag(request: Request) -> RagService:
    runtime = getattr(request.app.state, "memory_runtime", None)
    if runtime is None:  # started without her memory (`app/stores.py`)
        raise HTTPException(status_code=503, detail="Knowledge is unavailable.")
    rag: RagService = runtime.rag
    return rag


class TextDocument(BaseModel):
    title: str
    text: str


# The body as sent: the text's own limit, with room for JSON's escapes (a line break
# is two characters there, a quote two).
_MAX_TEXT_BODY_BYTES = 2 * MAX_TEXT_BYTES


@router.get("")
async def list_documents(request: Request) -> dict[str, Any]:
    docs = await _rag(request).list_documents()
    return {"documents": [d.model_dump(mode="json") for d in docs]}


@router.post("")
async def add_document(request: Request, file: Annotated[UploadFile, File()]) -> RagDocument:
    rag = _rag(request)
    data = await read_or_refuse(file)
    suffix = Path(file.filename or "doc").suffix or ".txt"
    title = Path(file.filename or "Document").stem
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(data)
        tmp = Path(handle.name)
    try:
        return await rag.index_file(tmp, title=title)
    except RagError as exc:
        raise Refusal(422, exc.code, str(exc)) from exc
    except Exception as exc:  # a corrupt file / parser failure -> safe message, not a trace
        logger.exception("document index failed")
        raise Refusal(422, "unreadable_file", "That file could not be read.") from exc
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/text")
async def add_text_document(request: Request) -> RagDocument:
    """Takes a `TextDocument`, read here rather than declared, so a body far over the
    limit is refused by its size before any of it is parsed."""
    raw = await read_body_capped(request, _MAX_TEXT_BODY_BYTES)
    if raw is None:
        raise Refusal(422, "text_too_long", TEXT_TOO_LONG)
    try:
        body = TextDocument.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc
    try:
        return await _rag(request).index_text(body.title.strip() or "Untitled", body.text)
    except RagError as exc:
        raise Refusal(422, exc.code, str(exc)) from exc


@router.delete("/{document_id}")
async def delete_document(document_id: RowId, request: Request) -> dict[str, int]:
    await _rag(request).delete(document_id)
    return {"deleted": document_id}
