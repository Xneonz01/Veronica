"""Personality REST: read, edit, reset. Changes apply live to voice and text."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.refusals import Refusal
from app.services.persona import PersonaRejected, PersonaService, Surface
from app.services.persona.defaults import DEFAULT_PERSONALITY, MAX_PERSONALITY_CHARS

router = APIRouter(prefix="/persona", tags=["persona"])


def _persona(request: Request) -> PersonaService:
    service = getattr(request.app.state, "persona", None)
    if service is None:  # pragma: no cover - always wired in production
        raise HTTPException(status_code=503, detail="Her personality is unavailable.")
    persona: PersonaService = service
    return persona


class PersonaUpdate(BaseModel):
    personality: str


def _payload(service: PersonaService) -> dict[str, Any]:
    return {
        "personality": service.personality,
        "default": DEFAULT_PERSONALITY,
        "max_chars": MAX_PERSONALITY_CHARS,
        # The fixed part, shown read-only so the user can see what always applies.
        "scaffold_preview": service.system_prompt(Surface.text, tools_enabled=True),
    }


@router.get("")
async def get_persona(request: Request) -> dict[str, Any]:
    return _payload(_persona(request))


@router.put("")
async def set_persona(update: PersonaUpdate, request: Request) -> dict[str, Any]:
    service = _persona(request)
    try:
        await service.set_personality(update.personality)
    except PersonaRejected as exc:
        raise Refusal(422, exc.code, exc.detail) from exc
    return _payload(service)


@router.post("/reset")
async def reset_persona(request: Request) -> dict[str, Any]:
    service = _persona(request)
    await service.reset()
    return _payload(service)
