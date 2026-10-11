"""The capability pipeline: resolve, validate, policy, confirm, run, audit.

A failure here either lets an unapproved action through or leaks a raw exception to
the user, so each branch has a test.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.core.event_bus import InProcessEventBus
from app.kernel.capability import CapabilityRequest, CapabilityResult, Decision, RiskTier
from app.kernel.confirm import ConfirmationBroker
from app.kernel.errors import Code, safe_validation_detail
from app.kernel.kernel import CapabilityKernel
from app.kernel.policy import PolicyMode, PolicyStore, PolicyViolation
from app.kernel.registry import Capability, CapabilityRegistry, to_tool_schema


class EchoArgs(BaseModel):
    model_config = {"extra": "forbid"}
    text: str


class _RecordingAudit:
    """Captures audit calls so tests can assert what the kernel recorded."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)

    def record_detached(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)

    @property
    def decisions(self) -> list[Decision]:
        return [row["decision"] for row in self.rows]


def _capability(
    handler: Any, *, risk: RiskTier = RiskTier.safe, timeout_s: float = 5.0
) -> Capability:
    return Capability(
        name="test.echo",
        description="Echo the text back.",
        args_model=EchoArgs,
        risk=risk,
        handler=handler,
        timeout_s=timeout_s,
    )


def _build(capability: Capability, audit: _RecordingAudit) -> CapabilityKernel:
    bus = InProcessEventBus()
    registry = CapabilityRegistry()
    registry.register(capability)
    return CapabilityKernel(
        registry, PolicyStore(bus), ConfirmationBroker(bus, timeout_s=0.05), audit, bus
    )


async def _echo(args: EchoArgs) -> CapabilityResult:
    return CapabilityResult(ok=True, detail=args.text)


# -- registry / schema ---------------------------------------------------------


def test_schema_is_derived_from_the_args_model() -> None:
    schema = to_tool_schema(_capability(_echo))
    function = schema["function"]
    assert function["name"] == "test_echo"  # dotted id -> underscored wire name
    assert function["parameters"]["properties"]["text"]["type"] == "string"
    assert function["parameters"].get("additionalProperties") is False  # extra="forbid"
    assert "title" not in function["parameters"]  # trimmed: it only costs prompt tokens


def test_nested_args_model_is_rejected_at_registration() -> None:
    # Nested models emit $defs/$ref, which the tool-schema format cannot carry. Failing
    # here means a bad capability breaks at import, not mid-conversation.
    class Inner(BaseModel):
        value: str

    class Outer(BaseModel):
        inner: Inner

    registry = CapabilityRegistry()
    with pytest.raises(ValueError, match="flat"):
        registry.register(
            Capability(
                name="test.nested",
                description="x",
                args_model=Outer,
                risk=RiskTier.safe,
                handler=_echo,
            )
        )


def test_registry_lookups_and_duplicate_guard() -> None:
    registry = CapabilityRegistry()
    registry.register(_capability(_echo))
    assert registry.get("test.echo") is not None
    assert registry.by_tool_name("test_echo") is not None
    assert registry.get("nope") is None
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_capability(_echo))


def test_validation_detail_never_echoes_the_offending_value() -> None:
    # ValidationError.errors() carries the input — which could be a password.
    try:
        EchoArgs.model_validate({"text": 123, "leaked": "hunter2"})
    except ValidationError as exc:
        detail = safe_validation_detail(exc)
    assert "hunter2" not in detail
    assert "123" not in detail


# -- pipeline ------------------------------------------------------------------


async def test_unknown_capability_is_a_typed_result_not_an_exception() -> None:
    audit = _RecordingAudit()
    kernel = _build(_capability(_echo), audit)
    result = await kernel.execute(CapabilityRequest(name="nope.missing", actor="veronica"))
    assert result.ok is False
    assert result.code == Code.unknown_capability
    assert audit.decisions == [Decision.failed]


async def test_bad_args_are_rejected_without_a_raw_validation_error() -> None:
    audit = _RecordingAudit()
    kernel = _build(_capability(_echo), audit)
    result = await kernel.execute(
        CapabilityRequest(name="test.echo", args={"wrong": 1}, actor="veronica")
    )
    assert result.ok is False and result.code == Code.invalid_args
    assert "ValidationError" not in result.detail


async def test_safe_capability_auto_allows_and_audits() -> None:
    audit = _RecordingAudit()
    kernel = _build(_capability(_echo), audit)
    result = await kernel.execute(
        CapabilityRequest(name="test.echo", args={"text": "hi"}, actor="veronica")
    )
    assert result.ok and result.detail == "hi"
    assert audit.decisions == [Decision.allowed]


async def test_handler_exception_becomes_a_typed_failure() -> None:
    async def boom(_args: EchoArgs) -> CapabilityResult:
        raise RuntimeError("internal detail that must not leak")

    audit = _RecordingAudit()
    kernel = _build(_capability(boom), audit)
    result = await kernel.execute(
        CapabilityRequest(name="test.echo", args={"text": "hi"}, actor="veronica")
    )
    assert result.ok is False and result.code == Code.capability_failed
    assert "internal detail" not in result.detail
    assert audit.decisions == [Decision.failed]


async def test_slow_handler_times_out() -> None:
    async def hang(_args: EchoArgs) -> CapabilityResult:
        await asyncio.sleep(5)
        return CapabilityResult(ok=True)

    audit = _RecordingAudit()
    kernel = _build(_capability(hang, timeout_s=0.02), audit)
    result = await kernel.execute(
        CapabilityRequest(name="test.echo", args={"text": "hi"}, actor="veronica")
    )
    assert result.ok is False and result.code == Code.capability_timeout


async def test_cancellation_propagates_and_is_still_audited() -> None:
    # Barge-in cancels the reply task, which cancels the capability. Swallowing the
    # CancelledError into a result would leave barge-in unable to interrupt.
    started = asyncio.Event()

    async def hang(_args: EchoArgs) -> CapabilityResult:
        started.set()
        await asyncio.sleep(5)
        return CapabilityResult(ok=True)

    audit = _RecordingAudit()
    kernel = _build(_capability(hang, timeout_s=10), audit)
    task = asyncio.create_task(
        kernel.execute(CapabilityRequest(name="test.echo", args={"text": "hi"}, actor="v"))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert audit.decisions == [Decision.cancelled]


# -- policy --------------------------------------------------------------------


async def test_denied_policy_blocks_execution() -> None:
    ran = False

    async def handler(_args: EchoArgs) -> CapabilityResult:
        nonlocal ran
        ran = True
        return CapabilityResult(ok=True)

    bus = InProcessEventBus()
    registry = CapabilityRegistry()
    capability = _capability(handler)
    registry.register(capability)
    policy = PolicyStore(bus, {"test.echo": "deny"})
    audit = _RecordingAudit()
    kernel = CapabilityKernel(registry, policy, ConfirmationBroker(bus), audit, bus)

    result = await kernel.execute(
        CapabilityRequest(name="test.echo", args={"text": "hi"}, actor="veronica")
    )
    assert result.ok is False and result.code == Code.policy_denied
    assert ran is False  # the handler never ran
    assert audit.decisions == [Decision.denied]


async def test_dangerous_can_never_be_auto_allowed() -> None:
    bus = InProcessEventBus()
    capability = _capability(_echo, risk=RiskTier.dangerous)
    # Even seeded from config, a dangerous capability still requires confirmation.
    seeded = PolicyStore(bus, {"test.echo": "auto_allow"})
    assert seeded.mode_for(capability) is PolicyMode.confirm
    with pytest.raises(PolicyViolation):
        await seeded.set_mode(capability, PolicyMode.auto_allow)


async def test_policy_change_is_live_and_broadcasts() -> None:
    bus = InProcessEventBus()
    modes: list[str] = []

    async def collect(event: Any) -> None:
        modes.append(event.payload["mode"])

    bus.subscribe("policy.changed", collect)
    capability = _capability(_echo)
    policy = PolicyStore(bus)
    assert policy.mode_for(capability) is PolicyMode.auto_allow  # safe default
    await policy.set_mode(capability, PolicyMode.deny)
    assert policy.mode_for(capability) is PolicyMode.deny
    assert modes == ["deny"]


class _SlowAudit(_RecordingAudit):
    """Writes each row only when released, as a write waiting on the store's lock does."""

    def __init__(self) -> None:
        super().__init__()
        self.writing = asyncio.Event()
        self.release = asyncio.Event()

    async def record(self, **kwargs: Any) -> None:
        self.writing.set()
        await self.release.wait()
        self.rows.append(kwargs)


@pytest.mark.parametrize(
    ("name", "decision"),
    [("test.echo", Decision.allowed), ("test.missing", Decision.failed)],
    ids=["ran", "refused"],
)
async def test_an_action_cut_off_while_its_row_is_written_is_still_recorded(
    name: str, decision: Decision
) -> None:
    # The action ran, or was refused; the turn was cancelled while its audit row waited
    # on the store. The cancellation reached the write, which rolled back: no record.
    audit = _SlowAudit()
    kernel = _build(_capability(_echo), audit)
    task = asyncio.create_task(
        kernel.execute(CapabilityRequest(name=name, args={"text": "hi"}, actor="t"))
    )
    await audit.writing.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    audit.release.set()
    for _ in range(5):
        await asyncio.sleep(0)

    assert audit.decisions == [decision]


async def test_a_capability_cut_off_while_running_is_recorded_as_perhaps_done() -> None:
    # A handler already running in a thread isn't stopped by a cancellation, so the
    # record can't say it didn't happen.
    started = asyncio.Event()

    async def slow(_args: EchoArgs) -> CapabilityResult:
        started.set()
        await asyncio.sleep(10)
        return CapabilityResult(ok=True)

    audit = _RecordingAudit()
    kernel = _build(_capability(slow), audit)
    task = asyncio.create_task(
        kernel.execute(CapabilityRequest(name="test.echo", args={"text": "hi"}, actor="t"))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert audit.rows[-1]["decision"] is Decision.cancelled
    assert "may have finished" in audit.rows[-1]["result"].detail
