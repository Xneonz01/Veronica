"""The confirmation gate: silence is never consent.

Every path that can end with nobody answering (timeout, shutdown, cancellation)
must end without the action running, so each one gets its own test.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.event_bus import InProcessEventBus
from app.kernel.capability import CapabilityRequest, CapabilityResult, Decision, RiskTier
from app.kernel.confirm import ConfirmationBroker
from app.kernel.errors import Code
from app.kernel.kernel import CapabilityKernel
from app.kernel.policy import PolicyStore
from app.kernel.registry import Capability, CapabilityRegistry
from app.services.tools.render import render_result


class WriteArgs(BaseModel):
    model_config = {"extra": "forbid"}
    path: str


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)

    def record_detached(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


def _harness(timeout_s: float = 5.0) -> tuple[CapabilityKernel, ConfirmationBroker, _Audit, list]:
    bus = InProcessEventBus()
    prompts: list[Any] = []

    async def capture(event: Any) -> None:
        prompts.append(event.payload)

    bus.subscribe("capability.confirm_required", capture)

    ran: list[str] = []

    async def handler(args: WriteArgs) -> CapabilityResult:
        ran.append(args.path)
        return CapabilityResult(ok=True, detail="written")

    registry = CapabilityRegistry()
    registry.register(
        Capability(
            name="files.write",
            description="Write a file.",
            args_model=WriteArgs,
            risk=RiskTier.guarded,  # guarded -> confirmation required
            handler=handler,
            summarize=lambda a: f"write {a.path}",
        )
    )
    broker = ConfirmationBroker(bus, timeout_s=timeout_s)
    audit = _Audit()
    kernel = CapabilityKernel(registry, PolicyStore(bus), broker, audit, bus)
    return kernel, broker, audit, [prompts, ran]


async def _request(kernel: CapabilityKernel) -> asyncio.Task[CapabilityResult]:
    task = asyncio.create_task(
        kernel.execute(
            CapabilityRequest(name="files.write", args={"path": "C:/x.txt"}, actor="veronica")
        )
    )
    await asyncio.sleep(0.01)  # let it reach the gate and publish the prompt
    return task


async def test_approval_lets_the_action_run() -> None:
    kernel, broker, audit, (prompts, ran) = _harness()
    task = await _request(kernel)

    assert len(prompts) == 1 and prompts[0]["summary"] == "write C:/x.txt"
    assert broker.resolve(prompts[0]["token"], approved=True) is True
    result = await task

    assert result.ok and ran == ["C:/x.txt"]
    assert audit.rows[-1]["decision"] is Decision.confirmed
    assert audit.rows[-1]["confirm_via"] == "ui"


async def test_denial_blocks_the_action() -> None:
    kernel, broker, audit, (prompts, ran) = _harness()
    task = await _request(kernel)

    broker.resolve(prompts[0]["token"], approved=False)
    result = await task

    assert result.ok is False and result.code == Code.confirm_denied
    assert ran == []  # the handler never ran
    assert audit.rows[-1]["decision"] is Decision.denied


async def test_no_answer_denies_it_fails_closed() -> None:
    # Silence is not consent: an unanswered prompt times out as a denial.
    kernel, _broker, audit, (_prompts, ran) = _harness(timeout_s=0.05)
    task = await _request(kernel)
    result = await task

    assert result.ok is False and result.code == Code.confirm_timeout
    assert ran == []
    assert audit.rows[-1]["decision"] is Decision.denied


async def test_a_prompt_nobody_could_see_fails_closed_but_isnt_a_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publishing the prompt failed: denied at once, as it must be, but said as "You
    declined that" though nobody was asked (mutation: the words and the record of a no)."""
    kernel, broker, audit, (_prompts, ran) = _harness()

    class _Unreachable:
        async def publish(self, _event: Any) -> None:
            raise RuntimeError("the event lane is down")

    monkeypatch.setattr(broker, "_bus", _Unreachable())
    result = await (await _request(kernel))

    assert (result.ok, result.code) == (False, Code.confirm_unavailable)
    assert result.detail == "I couldn't ask you about that, so I didn't do it."
    assert ran == [] and audit.rows[-1]["decision"] is Decision.failed
    # And the model reads that nobody was asked, not that the user said no.
    read = render_result("files.write", result)
    assert read == "Not done: the user couldn't be asked, so nothing changed."


async def test_a_token_cannot_be_replayed() -> None:
    kernel, broker, _audit, (prompts, _ran) = _harness()
    task = await _request(kernel)
    token = prompts[0]["token"]

    assert broker.resolve(token, approved=True) is True
    assert broker.resolve(token, approved=True) is False  # single-use
    await task


def test_an_unknown_token_is_ignored() -> None:
    _kernel, broker, _audit, _ = _harness()
    assert broker.resolve("not-a-real-token", approved=True) is False


async def test_shutdown_denies_everything_outstanding() -> None:
    kernel, broker, _audit, (_prompts, ran) = _harness(timeout_s=30)
    task = await _request(kernel)

    await broker.aclose()
    result = await task

    assert result.ok is False and ran == []


async def test_cancellation_clears_the_prompt_and_propagates() -> None:
    # Barge-in mid-confirmation: the request must not linger as a stale prompt.
    kernel, broker, audit, (prompts, ran) = _harness(timeout_s=30)
    task = await _request(kernel)
    assert len(broker.pending()) == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert broker.pending() == []
    assert ran == []
    # It was asked, so it is on the record: this used to leave no audit row at all.
    assert audit.rows[-1]["decision"] is Decision.cancelled
    assert audit.rows[-1]["capability"] == "files.write"


async def test_pending_lets_a_reloaded_surface_resync() -> None:
    kernel, broker, _audit, (prompts, _ran) = _harness(timeout_s=30)
    task = await _request(kernel)

    pending = broker.pending()
    assert len(pending) == 1
    assert pending[0].capability == "files.write"
    assert pending[0].expires_in_ms > 0
    assert pending[0].token == prompts[0]["token"]

    broker.resolve(pending[0].token, approved=False)
    await task


async def test_a_prompt_carries_the_id_its_request_gave_and_the_record_does_not() -> None:
    """A surface finds its own prompt by it (bug 36); it says nothing about the action,
    so it stays out of the record (mutation: drop it on the way)."""
    kernel, broker, audit, (_prompts, _ran) = _harness(timeout_s=30)
    request = CapabilityRequest(
        name="files.write", args={"path": "C:/x.txt"}, actor="veronica:voice", request_id="call-7"
    )
    task = asyncio.create_task(kernel.execute(request))
    await asyncio.sleep(0.01)

    [pending] = broker.pending()
    assert pending.request_id == "call-7"
    broker.resolve(pending.token, approved=False)
    await task
    assert "call-7" not in str(audit.rows)
