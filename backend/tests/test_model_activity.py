"""Whether the model is serving someone: what the learning worker waits on."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.model_activity import ModelActivity


async def _returns_within(awaitable: object, seconds: float) -> bool:
    try:
        await asyncio.wait_for(awaitable, seconds)  # type: ignore[arg-type]
    except TimeoutError:
        return False
    return True


async def test_a_model_nobody_used_for_the_window_is_quiet_at_once() -> None:
    activity = ModelActivity()
    assert await _returns_within(activity.quiet(0), 0.05)


async def test_quiet_waits_out_a_request_in_flight() -> None:
    activity = ModelActivity()
    with activity.busy():
        assert not await _returns_within(activity.quiet(0), 0.05)
    assert await _returns_within(activity.quiet(0), 0.05)


async def test_quiet_counts_the_window_from_when_the_last_request_ended() -> None:
    activity = ModelActivity()
    await asyncio.sleep(0.2)  # long past the window, counted from when it was made
    started = time.monotonic()
    with activity.busy():
        pass
    await activity.quiet(0.15)
    # Read on the clock `quiet` reads, from before the request ended: never less.
    assert time.monotonic() - started >= 0.15


async def test_a_waiter_wakes_when_the_request_it_waited_on_ends() -> None:
    activity = ModelActivity()
    release = asyncio.Event()

    async def request() -> None:
        with activity.busy():
            await release.wait()

    running = asyncio.create_task(request())
    await asyncio.sleep(0)
    waiter = asyncio.create_task(activity.quiet(0))
    await asyncio.sleep(0.05)
    assert not waiter.done()
    release.set()
    await asyncio.wait_for(waiter, 0.5)
    await running


async def test_quiet_waits_for_every_overlapping_request() -> None:
    activity = ModelActivity()
    with activity.busy():
        with activity.busy():
            pass
        assert not await _returns_within(activity.quiet(0), 0.05)
    assert await _returns_within(activity.quiet(0), 0.05)


async def test_a_request_that_fails_still_ends() -> None:
    activity = ModelActivity()
    with pytest.raises(RuntimeError), activity.busy():
        raise RuntimeError("the model went away")
    assert await _returns_within(activity.quiet(0), 0.05)


async def test_claimed_returns_once_a_request_starts() -> None:
    """What learning watches while it extracts, to give way to a reply."""
    activity = ModelActivity()
    watching = asyncio.create_task(activity.claimed())
    await asyncio.sleep(0.05)
    assert not watching.done()
    with activity.busy():
        await asyncio.wait_for(watching, 0.5)
        assert await _returns_within(activity.claimed(), 0.05)  # one already streaming
