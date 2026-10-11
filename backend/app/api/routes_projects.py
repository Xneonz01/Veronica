"""Projects REST: create, rename, delete, and file a conversation into one.

A project is its own table (`services/chat/projects.py`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.ids import BodyRowId, RowId
from app.services.chat.projects import ProjectStore

router = APIRouter(prefix="/projects", tags=["projects"])


def _projects(request: Request) -> ProjectStore:
    store = getattr(request.app.state, "project_store", None)
    if store is None:  # started without her memory (`app/stores.py`)
        raise HTTPException(status_code=503, detail="Projects are unavailable.")
    project_store: ProjectStore = store
    return project_store


class ProjectName(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class Assignment(BaseModel):
    conversation_id: BodyRowId
    project_id: BodyRowId | None = None  # null files it back out of any project


@router.get("")
async def list_projects(request: Request) -> dict[str, Any]:
    rows = await _projects(request).list()
    return {"projects": [row.model_dump(mode="json") for row in rows]}


@router.post("")
async def create_project(body: ProjectName, request: Request) -> dict[str, Any]:
    project = await _projects(request).create(body.name)
    return {"project": project.model_dump(mode="json")}


@router.patch("/{project_id}")
async def rename_project(project_id: RowId, body: ProjectName, request: Request) -> dict[str, Any]:
    if not await _projects(request).rename(project_id, body.name):
        raise HTTPException(status_code=404, detail="That project is no longer here.")
    return {"project_id": project_id, "name": body.name}


@router.delete("/{project_id}")
async def delete_project(project_id: RowId, request: Request) -> dict[str, int]:
    """Delete the project only; its conversations survive, unfiled.

    Deleting a folder should never quietly delete the work inside it.
    """
    if not await _projects(request).delete(project_id):
        raise HTTPException(status_code=404, detail="That project is no longer here.")
    return {"deleted": project_id}


@router.post("/assign")
async def assign_conversation(body: Assignment, request: Request) -> dict[str, Any]:
    store = _projects(request)
    if body.project_id is not None and await store.get(body.project_id) is None:
        raise HTTPException(status_code=404, detail="That project is no longer here.")
    if not await store.assign(body.conversation_id, body.project_id):
        raise HTTPException(status_code=404, detail="That conversation is no longer here.")
    return {"conversation_id": body.conversation_id, "project_id": body.project_id}
