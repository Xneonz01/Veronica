"""How a surface finds the prompt its own request raised (`ToolBroker.prompt_for`): by the
id the request carried, so a chat, or another call, asking about the same action at the
same moment never has its prompt answered here (bug 36)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.kernel.registry import CapabilityRegistry
from app.services.tools import ToolBroker


async def test_a_prompt_raised_by_chat_is_never_answered_on_the_call() -> None:
    # Chat and a call can ask at the same moment; a "yes" on the call was taken as the
    # answer to whichever prompt for that action came first.
    chat_prompt = SimpleNamespace(
        actor="veronica:chat",
        capability="files.write",
        token="t-chat",
        summary="write x",
        request_id="chat-1",
    )
    kernel = SimpleNamespace(pending_confirmations=lambda: [chat_prompt])
    tools = ToolBroker(kernel, CapabilityRegistry(), actor="veronica:voice")  # type: ignore[arg-type]
    running = asyncio.get_running_loop().create_future()  # the call's own request, still open

    found = await tools.prompt_for("call-1", running, timeout=0.05)
    assert found is None
    running.cancel()


async def test_two_calls_asking_about_one_action_each_find_their_own_prompt() -> None:
    """Bug 36: both act as "veronica:voice", and matched by actor and tool, each call
    took the first prompt, so a "yes" said in one answered the other's (mutation: match
    by actor and tool)."""

    def prompt(token: str, request_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            actor="veronica:voice",
            capability="files.write",
            token=token,
            summary="write x",
            request_id=request_id,
        )

    prompts = [prompt("t-first", "first"), prompt("t-second", "second")]
    kernel = SimpleNamespace(pending_confirmations=lambda: prompts)
    tools = ToolBroker(kernel, CapabilityRegistry(), actor="veronica:voice")  # type: ignore[arg-type]
    running = asyncio.get_running_loop().create_future()

    found = await tools.prompt_for("second", running, timeout=0.05)

    assert found is not None and found.token == "t-second"
    running.cancel()


async def test_a_request_refused_before_any_prompt_is_not_waited_on() -> None:
    # Bad arguments are refused before a prompt exists; the wait used to run its whole
    # two seconds, in silence, before the turn went on.
    kernel = SimpleNamespace(pending_confirmations=lambda: [])
    tools = ToolBroker(kernel, CapabilityRegistry(), actor="veronica:voice")  # type: ignore[arg-type]
    refused = asyncio.get_running_loop().create_future()
    refused.set_result("refused")

    started = asyncio.get_running_loop().time()
    found = await tools.prompt_for("call-1", refused)

    assert found is None
    assert asyncio.get_running_loop().time() - started < 0.5
