"""Typed events that flow across the in-process event bus."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Base envelope for everything published on the event bus."""

    type: str  # e.g. "memory.updated"
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
