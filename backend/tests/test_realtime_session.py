"""RealtimeSession orchestration — deterministic, fake adapters, no real models."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.adapters.llm.ollama_errors import OllamaModelFailedError, OllamaModelNoToolsError
from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.services.voice.realtime.session import RealtimeSession
from app.services.voice.turn import TurnEvent
from tests.fakes import (
    FakeDetector,
    FakeLLM,
    FakeSTT,
    FakeTTS,
    FakeWS,
    ListMetricsSink,
    build_fake_runtime,
)

# --- session builder ---


def make_session(
    ws: FakeWS,
    *,
    detector: FakeDetector,
    stt: FakeSTT,
    tts: FakeTTS,
    llm: FakeLLM,
    sink: ListMetricsSink,
    prefetch: Any = None,
    memory: Any = None,
) -> RealtimeSession:
    runtime = build_fake_runtime(stt=stt, tts=tts, llm=llm)
    settings = get_settings()
    kwargs: dict[str, Any] = {"metrics_sink": sink, "detector": detector}
    if memory is not None:
        kwargs["memory"] = memory
        kwargs["prefetch"] = memory.assembler.assemble
    if prefetch is not None:
        kwargs["prefetch"] = prefetch
    return RealtimeSession(
        ws,  # type: ignore[arg-type]
        runtime,
        InProcessEventBus(),
        settings,
        # A callable, not a string: the session reads the persona per turn so an edit
        # applies mid-call.
        persona=lambda: "You are Veronica.",
        **kwargs,
    )


# --- helpers ---


async def wait_until(predicate: Any, max_rounds: int = 2000) -> None:
    # A tiny real sleep (not sleep(0)) so threaded dependencies, chiefly aiosqlite's
    # worker thread behind the memory store, get CPU time to resolve their futures
    # between checks. sleep(0) can spin the loop and starve that thread.
    for _ in range(max_rounds):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition was not met")


def events(ws: FakeWS) -> list[dict[str, Any]]:
    return [json.loads(t) for t in ws.sent_text]


def states(ws: FakeWS) -> list[str]:
    return [e["state"] for e in events(ws) if e["type"] == "veronica_state"]


def _utterance(start: int = 1) -> list[list[TurnEvent]]:
    # one frame each: speech_start, (silence), speech_end
    return [[TurnEvent.speech_start], [], [TurnEvent.speech_end]]


async def feed(session: RealtimeSession, count: int) -> None:
    for _ in range(count):
        session.audio_in.put_nowait(b"\x00\x00" * 320)  # 20 ms of silence


async def start_call(session: RealtimeSession, ws: FakeWS) -> None:
    """Begin a call and wait until background warmup finishes (state -> listening)."""
    await session._on_call_start()
    await wait_until(lambda: "listening" in states(ws))


# --- tests ---


async def test_full_turn_event_and_state_order() -> None:
    ws = FakeWS()
    sink = ListMetricsSink()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT(["hello", "hello there"], "hello there"),
        tts=FakeTTS(),
        llm=FakeLLM(["Hi. ", "All good."]),
        sink=sink,
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: any(e["type"] == "metrics" for e in events(ws)))
    await wait_until(lambda: session.audio_out.empty() and states(ws)[-1:] == ["listening"])

    assert states(ws) == [
        "connecting",
        "warming_up",
        "listening",
        "thinking",
        "speaking",
        "listening",
    ]
    finals = [e["text"] for e in events(ws) if e["type"] == "asr_final"]
    assert finals == ["hello there"]
    assert any(e["type"] == "assistant_token" for e in events(ws))
    assert ws.sent_bytes  # audio was streamed back
    assert len(sink.rows) == 1
    await session._stop_call()


async def test_warming_up_precedes_listening() -> None:
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "hi"),
        tts=FakeTTS(),
        llm=FakeLLM(["Hello."]),
        sink=ListMetricsSink(),
    )
    await session._on_call_start()
    await wait_until(lambda: "listening" in states(ws))

    seq = states(ws)
    assert seq[:3] == ["connecting", "warming_up", "listening"]  # cold load is visible
    await session._stop_call()


async def test_sentence_chunk_fires_tts_before_llm_done() -> None:
    """She starts speaking the first sentence while the model is still writing.

    The model is held open after its deltas. Waiting for "llm_done" and looking back
    proved this only while the model was read at the pace of speech; it is now read
    ahead of the speaker (`reply_cap.read_ahead`), so an instant fake is done before
    anything is said.
    """
    ws = FakeWS()
    trace: list[Any] = []
    block = asyncio.Event()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "go"),
        tts=FakeTTS(trace=trace),
        llm=FakeLLM(["First sentence. ", "second part."], block=block, trace=trace),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)

    await wait_until(lambda: ("synth", "First sentence.") in trace)
    assert "llm_done" not in trace  # spoken with the model still mid-reply
    block.set()
    await wait_until(lambda: "llm_done" in trace)
    await session._stop_call()


async def test_barge_in_cancels_reply_and_restarts_listening() -> None:
    ws = FakeWS()
    block = asyncio.Event()
    # script: first utterance (start, silence, end), then an interrupting speech_start
    script = [[TurnEvent.speech_start], [], [TurnEvent.speech_end], [TurnEvent.speech_start]]
    session = make_session(
        ws,
        detector=FakeDetector(script),
        stt=FakeSTT([], "play music"),
        tts=FakeTTS(),
        # holds the reply open after the sentence, which the next word lets through
        llm=FakeLLM(["Sure thing.", " Playing"], block=block),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)  # first utterance -> reply starts, reaches speaking, then blocks
    await wait_until(lambda: states(ws)[-1:] == ["speaking"])
    respond_task = session._respond_task
    assert respond_task is not None

    await feed(session, 1)  # the interrupting speech frame -> barge-in
    # playback_flush is emitted just after barge_in, so waiting on it covers both
    await wait_until(lambda: any(e["type"] == "playback_flush" for e in events(ws)))

    kinds = [e["type"] for e in events(ws)]
    assert "barge_in" in kinds
    assert "playback_flush" in kinds
    assert states(ws)[-1] == "listening"
    await wait_until(lambda: session.utterance_q is not None)  # a new utterance started
    await wait_until(lambda: respond_task.cancelled() or respond_task.done())

    block.set()
    await session._stop_call()


async def test_metrics_emitted() -> None:
    ws = FakeWS()
    sink = ListMetricsSink()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "what time is it"),
        tts=FakeTTS(),
        llm=FakeLLM(["It is noon."]),
        sink=sink,
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: len(sink.rows) == 1)

    row = sink.rows[0]
    assert row["ttft_ms"] is not None
    assert row["first_audio_ms"] is not None
    assert row["total_ms"] is not None
    assert any(e["type"] == "metrics" for e in events(ws))
    await session._stop_call()


async def test_a_missing_chat_model_is_said_as_such_and_the_call_listens_on() -> None:
    """Bug 30: named, so the app can say which model to pull (mutation: report it as a
    failed reply)."""
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "hello"),
        tts=FakeTTS(),
        llm=FakeLLM([], missing_model="qwen3.5:9b"),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(
        lambda: any(e["type"] == "error" for e in events(ws)) and states(ws)[-1:] == ["listening"]
    )

    errors = [e for e in events(ws) if e["type"] == "error"]
    assert (errors[0]["kind"], errors[0]["model"]) == ("llm_model_missing", "qwen3.5:9b")
    assert states(ws)[-1] == "listening"
    await session._stop_call()


async def test_ollama_unreachable_surfaces_clean_error() -> None:
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "hello"),
        tts=FakeTTS(),
        llm=FakeLLM([], unreachable=True),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(
        lambda: any(e["type"] == "error" for e in events(ws)) and states(ws)[-1:] == ["listening"]
    )

    errors = [e for e in events(ws) if e["type"] == "error"]
    assert errors[0]["kind"] == "llm_unreachable"
    assert states(ws)[-1] == "listening"  # no crash, ready for the next turn
    await session._stop_call()


async def test_stt_failure_surfaces_and_recovers() -> None:
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], None, boom=True),
        tts=FakeTTS(),
        llm=FakeLLM(["unused"]),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(
        lambda: any(e["type"] == "error" and e.get("kind") == "stt_failed" for e in events(ws))
    )
    assert states(ws)[-1] == "listening"  # recovered, no crash, no swallow
    await session._stop_call()


async def test_reply_failure_surfaces_and_recovers() -> None:
    # A non-Ollama failure mid-reply (e.g. TTS) must surface a recoverable error and
    # return to listening — not swallow the exception and hang in thinking/speaking.
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "hello"),
        tts=FakeTTS(),
        llm=FakeLLM(["Hi.", " Let me"], crash=True),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(
        lambda: (
            any(e["type"] == "error" and e.get("kind") == "reply_failed" for e in events(ws))
            and states(ws)[-1:] == ["listening"]
        )
    )
    assert "speaking" in states(ws)  # the reply had started before it failed
    assert states(ws)[-1] == "listening"  # recovered, not stuck mid-reply
    await session._stop_call()


async def test_two_utterances_back_to_back() -> None:
    ws = FakeWS()
    script = [
        [TurnEvent.speech_start],
        [],
        [TurnEvent.speech_end],
        [TurnEvent.speech_start],
        [],
        [TurnEvent.speech_end],
    ]
    session = make_session(
        ws,
        detector=FakeDetector(script),
        stt=FakeSTT([], "hello"),
        tts=FakeTTS(),
        llm=FakeLLM(["Hi."]),
        sink=ListMetricsSink(),
    )
    finals = lambda: [e for e in events(ws) if e["type"] == "asr_final"]  # noqa: E731
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: len(finals()) == 1 and states(ws)[-1] == "listening")
    await feed(session, 3)
    await wait_until(lambda: len(finals()) == 2)
    assert not [e for e in events(ws) if e["type"] == "error"]  # no dangling/leaked failure
    await session._stop_call()


async def test_barge_in_records_barged_metrics() -> None:
    ws = FakeWS()
    block = asyncio.Event()
    script = [[TurnEvent.speech_start], [], [TurnEvent.speech_end], [TurnEvent.speech_start]]
    session = make_session(
        ws,
        detector=FakeDetector(script),
        stt=FakeSTT([], "play music"),
        tts=FakeTTS(),
        llm=FakeLLM(["Sure thing.", " Playing"], block=block),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: states(ws)[-1:] == ["speaking"])

    await feed(session, 1)  # interrupting frame -> barge-in
    # The row follows the flush, so it waits on neither the socket nor the disk.
    await wait_until(lambda: any(e["type"] == "metrics" for e in events(ws)))

    rows = [e for e in events(ws) if e["type"] == "metrics"]
    assert rows and rows[-1]["barged_in"] is True  # the interrupted turn was measured

    block.set()
    await session._stop_call()


async def test_prefetch_hook_invoked() -> None:
    ws = FakeWS()
    seen: list[str] = []

    async def spy(user_text: str) -> None:
        seen.append(user_text)

    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "remember this"),
        tts=FakeTTS(),
        llm=FakeLLM(["Okay."]),
        sink=ListMetricsSink(),
        prefetch=spy,
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: seen == ["remember this"])
    await session._stop_call()


async def test_teardown_leaves_no_tasks() -> None:
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "bye"),
        tts=FakeTTS(),
        llm=FakeLLM(["Goodbye."]),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: states(ws)[-1:] == ["listening"] and len(states(ws)) >= 4)
    await session._stop_call()

    assert session._ingest_task is None
    assert session._audio_out_task is None
    assert session._respond_task is None or session._respond_task.done()
    assert session._stt_task is None or session._stt_task.done()


class _OneTool:
    """A call's tools, as much of them as a model that refuses them sees: their list."""

    def schemas(self) -> list[dict[str, Any]]:
        parameters = {"type": "object", "properties": {}}
        return [
            {"type": "function", "function": {"name": "memory_recall", "parameters": parameters}}
        ]


@pytest.mark.parametrize(
    ("refusal", "kind"),
    [
        (OllamaModelNoToolsError("smollm:135m"), "llm_no_tools"),
        (
            OllamaModelFailedError("smollm:135m", "llama-server process has terminated"),
            "llm_cant_run",
        ),
    ],
)
async def test_a_refusal_asking_again_cant_mend_is_said_on_a_call(
    refusal: Exception, kind: str
) -> None:
    """Bug 45: both read as a failed reply (mutation: report them as reply_failed)."""
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT([], "hello"),
        tts=FakeTTS(),
        llm=FakeLLM([], refusal=refusal),
        sink=ListMetricsSink(),
    )
    session.tools = _OneTool()  # type: ignore[assignment]  # a call always offers tools
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: any(e["type"] == "error" for e in events(ws)))

    errors = [e for e in events(ws) if e["type"] == "error"]
    assert (errors[0]["kind"], errors[0]["model"]) == (kind, "smollm:135m")
    await session._stop_call()
