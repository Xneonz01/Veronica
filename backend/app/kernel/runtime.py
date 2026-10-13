"""Wiring for the capability kernel, mirroring `memory/runtime.py`.

The audit store borrows the memory store's single connection, so this is built
after the memory runtime and its `init()` is awaited after the memory one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.event_bus import EventBus
from app.kernel.audit import AuditStore
from app.kernel.audit_integrity import AuditIntegrity
from app.kernel.budget import Budget
from app.kernel.confirm import ConfirmationBroker
from app.kernel.kernel import CapabilityKernel
from app.kernel.policy import PolicyStore
from app.kernel.registry import CapabilityRegistry
from app.services.memory.store import MemoryStore


@dataclass(slots=True)
class KernelRuntime:
    """The constructed kernel components, shared via ``app.state``."""

    registry: CapabilityRegistry
    policy: PolicyStore
    confirmations: ConfirmationBroker
    audit: AuditStore
    integrity: AuditIntegrity
    kernel: CapabilityKernel

    async def init(self) -> None:
        """Create the audit tables (the memory store must already be open)."""
        await self.audit.init_schema()
        await self.integrity.init_schema()

    async def aclose(self) -> None:
        """Deny anything still awaiting confirmation, then let audit writes land."""
        await self.confirmations.aclose()
        await self.audit.drain()


def build_kernel_runtime(
    settings: Settings,
    bus: EventBus,
    store: MemoryStore,
    *,
    policy_path: Path | None = None,
) -> KernelRuntime:
    registry = CapabilityRegistry()
    policy = PolicyStore(bus, settings.capabilities.policy, path=policy_path)
    policy.load()  # saved choices layer over the config defaults
    audit = AuditStore(store)
    # The prompt screens what it shows itself (`kernel/display.py`).
    confirmations = ConfirmationBroker(bus, timeout_s=settings.capabilities.confirm_timeout_s)
    caps = settings.capabilities
    kernel = CapabilityKernel(
        registry,
        policy,
        confirmations,
        audit,
        bus,
        taint_min_words=caps.taint_min_words,
        budget=Budget(
            per_turn=caps.turn_budget, per_minute=caps.minute_budget, turn_total=caps.turn_total
        ),
    )
    return KernelRuntime(
        registry=registry,
        policy=policy,
        confirmations=confirmations,
        audit=audit,
        integrity=AuditIntegrity(store),
        kernel=kernel,
    )
