"""Capability providers: the concrete actions Veronica can take.

`register_capabilities` is the single registration point, so there is one place to
see everything she can do. Providers stay small; path safety lives in `paths.py` and
is shared rather than re-implemented per provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import Settings
from app.kernel.registry import CapabilityRegistry

if TYPE_CHECKING:
    from app.services.memory.runtime import MemoryRuntime


def register_capabilities(
    registry: CapabilityRegistry, settings: Settings, memory: MemoryRuntime | None = None
) -> None:
    """Register every capability. Called once during app startup.

    Memory first: the model has always been offered its memory tools ahead of the rest,
    and the order of a tool list is part of what it reads.
    """
    from app.capabilities.apps import register_apps
    from app.capabilities.files import register_files
    from app.capabilities.memory import register_memory
    from app.capabilities.protected import protected_for

    register_memory(registry, memory)
    register_files(registry, settings.capabilities, protected_for(settings))
    register_apps(registry, settings.capabilities)
