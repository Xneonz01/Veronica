"""Health endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.config import Settings
from app.core.problems import Problem
from app.core.version import APP_CHANNEL, APP_VERSION, APP_VERSION_LABEL

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness probe with the running version, the active profile, and anything keeping
    her from working fully (`core/problems.py`): `status` is "degraded" then, and each
    problem is a code and the few values its words need.

    The label is the one read from `CHANGELOG.yaml` at startup, so it describes the
    build that is running even if the file has been edited since.
    """
    settings: Settings = request.app.state.settings
    problems: list[Problem] = getattr(request.app.state, "problems", [])
    return {
        "status": "degraded" if problems else "ok",
        "app": settings.app.name,
        "version": APP_VERSION,
        "label": getattr(request.app.state, "version_label", APP_VERSION_LABEL),
        "channel": APP_CHANNEL,
        "env": settings.app.env,
        "profile": settings.profile,
        "problems": [problem.model_dump() for problem in problems],
    }
