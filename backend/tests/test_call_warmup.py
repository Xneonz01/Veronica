"""A call whose warm-up fails: it says why and lets go of everything it took, and a model
it needs that isn't on disk is named, found as the start-up finds it (bug 44). A card
without room for the call refuses it before anything loads (bug 46)."""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters import model_files
from app.adapters.llm.ollama_errors import OllamaModelFailedError
from app.services.voice.realtime import card_room
from tests.fakes import FakeDetector, FakeLLM, FakeSTT, FakeTTS, FakeWS, ListMetricsSink
from tests.test_realtime_session import events, make_session, start_call, states


async def test_warmup_failure_emits_error_and_unsubscribes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeWS()
    monkeypatch.setattr(model_files, "missing", lambda _settings: [])  # every model there

    class BoomManager:
        def to_load(self, _mode: Any) -> set[str]:
            return {"stt", "tts", "embedding"}

        async def set_mode(self, _mode: Any, *, load: bool = True) -> None:
            raise RuntimeError("gpu unavailable")

    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
    )
    session.runtime.manager = BoomManager()  # type: ignore[assignment]

    await session._on_call_start()
    assert session._warmup_task is not None
    await session._warmup_task  # run warmup to completion (it fails internally)

    errors = [e for e in events(ws) if e["type"] == "error"]
    assert errors[0]["kind"] == "warmup_failed"
    assert "listening" not in states(ws)  # never went live
    assert session._unsub is None  # forwarder unsubscribed -> no bus subscription leak
    await session._stop_call()


EVERY = {"stt", "tts", "embedding"}


async def _warm_up_failing(ws: FakeWS, waiting: set[str]) -> None:
    """A call whose warm-up fails on a model not on disk, with `waiting` still to load."""

    class NotOnDisk:
        def to_load(self, _mode: Any) -> set[str]:
            return waiting

        async def set_mode(self, _mode: Any, *, load: bool = True) -> None:
            raise FileNotFoundError("the model's snapshot isn't in the cache")

    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
    )
    session.runtime.manager = NotOnDisk()  # type: ignore[assignment]
    await session._on_call_start()
    assert session._warmup_task is not None
    await session._warmup_task
    await session._stop_call()


@pytest.mark.parametrize(
    ("absent", "waiting", "kind", "models"),
    [
        (["embedding", "rerank"], EVERY, "models_missing", "embedding"),
        (["tts", "stt"], EVERY, "models_missing", "tts,stt"),
        (["turn", "rerank"], EVERY, "warmup_failed", None),  # done without: not the cause
        # Her memory couldn't be opened, so no mode loads its model: not the cause.
        (["embedding"], {"stt", "tts"}, "warmup_failed", None),
        # In memory already, whatever the disk says now: not the cause either.
        (["tts"], {"stt", "embedding"}, "warmup_failed", None),
    ],
)
async def test_a_call_without_a_model_it_needs_says_which(
    monkeypatch: pytest.MonkeyPatch,
    absent: list[str],
    waiting: set[str],
    kind: str,
    models: str | None,
) -> None:
    """Bug 44: it failed as "check that the backend is running"; and only a model the
    warm-up would still have loaded is named, in words that name no other (mutations:
    always the general words; every model missing named)."""
    ws = FakeWS()
    listed = [model_files.Weights(code, code, code, lambda: None) for code in absent]
    monkeypatch.setattr(model_files, "missing", lambda _settings: listed)

    await _warm_up_failing(ws, waiting)

    [error] = [e for e in events(ws) if e["type"] == "error"]
    assert (error["kind"], error.get("models")) == (kind, models)
    if kind == "models_missing":
        assert "rerank" not in error["message"] and "turn" not in error["message"]


async def test_a_model_check_that_fails_leaves_the_general_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(mutation: let the check's own failure through)"""
    ws = FakeWS()

    def unreadable(_settings: object) -> list[model_files.Weights]:
        raise PermissionError("the model cache is closed to her")

    monkeypatch.setattr(model_files, "missing", unreadable)

    await _warm_up_failing(ws, EVERY)

    assert [e["kind"] for e in events(ws) if e["type"] == "error"] == ["warmup_failed"]


FULL = card_room.Room(total_mib=16_376, free_mib=4_538, adds_mib=4_762, takes_mib=13_083)
SMALL = card_room.Room(total_mib=12_288, free_mib=9_988, adds_mib=11_738, takes_mib=13_083)


@pytest.mark.parametrize(
    ("room", "kind", "figures"),
    [
        (FULL, "card_full", {"free_mib": "4538", "needs_mib": str(FULL.needs_mib)}),
        (SMALL, "card_small", {"total_mib": "12288", "needs_mib": str(SMALL.card_needs_mib)}),
    ],
)
async def test_a_card_without_room_refuses_the_call_before_it_loads_anything(
    monkeypatch: pytest.MonkeyPatch, room: card_room.Room, kind: str, figures: dict[str, str]
) -> None:
    """Bug 46: the call started, was paged in and out, and stopped answering; she couldn't
    say why. The first is 1.5 GiB held beside the call, as measured; the second a 12 GB card
    with nothing else on it (mutations: go ahead whatever the card holds; a small card
    told to close a program)."""
    ws = FakeWS()

    async def measured(_settings: object) -> card_room.Room:
        return room

    monkeypatch.setattr(card_room, "measure", measured)

    class Untouched:
        def to_load(self, _mode: Any) -> set[str]:
            return set()

        async def set_mode(self, _mode: Any, *, load: bool = True) -> None:
            raise AssertionError("a refused call loads nothing")

    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
    )
    session.runtime.manager = Untouched()  # type: ignore[assignment]
    await session._on_call_start()
    assert session._warmup_task is not None
    await session._warmup_task

    [error] = [e for e in events(ws) if e["type"] == "error"]
    assert error["kind"] == kind
    assert {key: error[key] for key in figures} == figures
    assert "listening" not in states(ws)
    assert session._unsub is None and not session._call_active  # let go of, as a failure
    await session._stop_call()


async def test_a_card_with_little_room_left_warns_and_the_call_goes_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(mutation: refuse a call the card has room for, only just)"""
    ws = FakeWS()

    async def measured(_settings: object) -> card_room.Room:
        # 574 left, as measured with 1 GiB held beside the call.
        return card_room.Room(total_mib=16_376, free_mib=5_300, adds_mib=4_726, takes_mib=13_083)

    monkeypatch.setattr(card_room, "measure", measured)
    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
    )

    await start_call(session, ws)  # waits until she listens

    assert [e["kind"] for e in events(ws) if e["type"] == "error"] == ["card_tight"]
    await session._stop_call()


async def test_a_model_that_couldnt_run_lets_the_call_go_live() -> None:
    """Bug 45: it failed the warm-up, as "check that the backend is running"; live, the
    call's first turn says what to close (mutation: let it fail the warm-up)."""
    ws = FakeWS()
    stopped = OllamaModelFailedError("qwen3.5:9b", "llama-server process has terminated")
    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM([], refusal=stopped),
        sink=ListMetricsSink(),
    )

    await start_call(session, ws)  # waits until she listens

    assert [e for e in events(ws) if e["type"] == "error"] == []
    await session._stop_call()
