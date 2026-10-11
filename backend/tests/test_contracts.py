"""Core contracts: interfaces import, ABCs are abstract, the event bus works."""

from __future__ import annotations

import pytest

from app.adapters.base import (
    EmbeddingProvider,
    LLMProvider,
    STTProvider,
    TTSProvider,
)
from app.core.event_bus import InProcessEventBus
from app.core.gpu_manager import GpuMode, GpuModelManager
from app.kernel.capability import CapabilityRequest, NotImplementedKernel
from app.schemas.events import Event


@pytest.mark.parametrize(
    "abc_cls",
    [STTProvider, LLMProvider, TTSProvider, EmbeddingProvider],
)
def test_provider_abcs_cannot_instantiate(abc_cls: type) -> None:
    """The provider ABCs must not be instantiable directly."""
    with pytest.raises(TypeError):
        abc_cls()


async def test_kernel_stub_refuses() -> None:
    """The null-object kernel, used where no real kernel is wired, refuses everything."""
    result = await NotImplementedKernel().execute(
        CapabilityRequest(name="files.read", actor="veronica")
    )
    assert result.ok is False
    assert result.detail == "I can't take actions here."


def test_gpu_mode_and_protocol_exist() -> None:
    """GpuMode exposes the operating modes; the manager Protocol is importable."""
    assert {m.value for m in GpuMode} == {"idle", "conversation"}
    assert GpuModelManager.__name__ == "GpuModelManager"


async def test_event_bus_delivers_then_unsubscribes() -> None:
    """A published event reaches its subscriber; unsubscribe stops delivery."""
    bus = InProcessEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    unsubscribe = bus.subscribe("system.ready", handler)
    await bus.publish(Event(type="system.ready", source="test"))
    assert len(received) == 1

    unsubscribe()
    await bus.publish(Event(type="system.ready", source="test"))
    assert len(received) == 1  # no delivery after unsubscribe


async def test_event_bus_isolates_handler_errors() -> None:
    """One subscriber raising must not stop delivery to the others."""
    bus = InProcessEventBus()
    received: list[Event] = []

    async def boom(event: Event) -> None:
        raise RuntimeError("handler failure")

    async def ok(event: Event) -> None:
        received.append(event)

    bus.subscribe("system.ready", boom)
    bus.subscribe("system.ready", ok)
    await bus.publish(Event(type="system.ready", source="test"))  # must not raise
    assert len(received) == 1
