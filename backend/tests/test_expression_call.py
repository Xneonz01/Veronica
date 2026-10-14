"""On a call, a sound tag reaches her voice and nothing else: not the transcript on
screen, not the call's history, not what learning reads after it. And the Expressive
voice switch reaches a call from the client, at its start and while it runs."""

from __future__ import annotations

from typing import Any

from app.services.persona.defaults import EXPRESSION_RULES
from app.services.voice.realtime import session_control
from app.services.voice.realtime.session import RealtimeSession
from tests.fakes import FakeDetector, FakeLLM, FakeSTT, FakeTTS, FakeWS, ListMetricsSink
from tests.test_realtime_session import (
    _utterance,
    events,
    feed,
    make_session,
    start_call,
    states,
    wait_until,
)

_REPLY = ["[la", "ugh] Okay, ", "that one got me. ", "*sighs* Fine."]


def _session(
    ws: FakeWS, trace: list[Any], seen: list[Any], tokens: list[str], **llm: Any
) -> RealtimeSession:
    return make_session(
        ws,
        detector=FakeDetector(_utterance()),
        stt=FakeSTT(["a joke"], "a joke"),
        tts=FakeTTS(trace=trace),
        llm=FakeLLM(tokens, seen=seen, **llm),
        sink=ListMetricsSink(),
    )


async def _one_turn(session: RealtimeSession, ws: FakeWS) -> None:
    await feed(session, 3)
    await wait_until(lambda: any(e["type"] == "metrics" for e in events(ws)))
    await wait_until(lambda: session.audio_out.empty() and states(ws)[-1:] == ["listening"])


def _shown(ws: FakeWS) -> str:
    return "".join(e["text"] for e in events(ws) if e["type"] == "assistant_token")


def _heard(trace: list[Any]) -> str:
    return " ".join(text for kind, text in trace if kind == "synth")


async def test_her_voice_laughs_and_nothing_shown_or_kept_says_so() -> None:
    ws, trace, seen = FakeWS(), [], []
    session = _session(ws, trace, seen, _REPLY)
    await start_call(session, ws)
    await _one_turn(session, ws)

    assert EXPRESSION_RULES in seen[-1][0]["content"]  # she was told she can
    assert "[laugh] Okay," in _heard(trace)  # after the warm-up's "Ready."
    assert "[sigh] Fine." in _heard(trace)
    assert _shown(ws) == "Okay, that one got me. Fine."
    assert session.history[-1] == {"role": "assistant", "content": "Okay, that one got me. Fine."}
    await session._stop_call()


async def test_switched_off_she_is_not_told_and_her_voice_makes_no_sound() -> None:
    ws, trace, seen = FakeWS(), [], []
    session = _session(ws, trace, seen, _REPLY)
    await session_control.dispatch(session, {"action": "call_start", "expressive": False})
    await wait_until(lambda: "listening" in states(ws))
    await _one_turn(session, ws)

    assert EXPRESSION_RULES not in seen[-1][0]["content"]
    assert "[" not in _heard(trace) and "*" not in _heard(trace)
    assert _shown(ws) == "Okay, that one got me. Fine."
    await session._stop_call()


async def test_a_reply_cut_short_is_kept_without_its_tags() -> None:
    # A reply that fails partway keeps what her voice said, from the ledger.
    ws, trace, seen = FakeWS(), [], []
    session = _session(ws, trace, seen, ["[laugh] Okay. ", "Then more"], crash=True)
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: any(e["type"] == "error" for e in events(ws)))
    await wait_until(lambda: states(ws)[-1:] == ["listening"])

    assert "[laugh] Okay." in _heard(trace)
    kept = [m["content"] for m in session.history if m["role"] == "assistant"]
    assert kept == ["Okay.…"]
    await session._stop_call()


async def test_the_switch_reaches_a_call_at_its_start_and_while_it_runs() -> None:
    ws = FakeWS()
    session = _session(ws, [], [], ["Hi."])
    assert session.expressive is True  # on until the client says otherwise

    await session_control.dispatch(session, {"action": "call_start", "expressive": False})
    assert session.expressive is False
    await session_control.dispatch(session, {"action": "call_options", "expressive": True})
    assert session.expressive is True
    for junk in ("no", 1, None, [False]):  # not a yes or a no: left as it is
        await session_control.dispatch(session, {"action": "call_options", "expressive": junk})
        assert session.expressive is True
    await session._stop_call()


async def test_a_new_call_without_the_setting_starts_from_the_default() -> None:
    ws = FakeWS()
    session = _session(ws, [], [], ["Hi."])
    await session_control.dispatch(session, {"action": "call_start", "expressive": False})
    await session._stop_call()
    await session_control.dispatch(session, {"action": "call_start"})
    assert session.expressive is True
    await session._stop_call()


async def test_she_never_laughs_where_she_says_she_is_sorry() -> None:
    ws, trace, seen = FakeWS(), [], []
    session = _session(
        ws, trace, seen, ["[laugh] Oh no, I'm so sorry. ", "[chuckle] Ha, good one."]
    )
    await start_call(session, ws)
    await _one_turn(session, ws)

    assert "Oh no, I'm so sorry." in _heard(trace)
    assert "[laugh]" not in _heard(trace)
    assert "[chuckle] Ha, good one." in _heard(trace)  # a laugh elsewhere in the reply stays
    await session._stop_call()


async def test_what_was_held_back_at_the_end_of_a_reply_is_still_said() -> None:
    ws, trace, seen = FakeWS(), [], []
    session = _session(ws, trace, seen, ["It costs five pounds (or so"])
    await start_call(session, ws)
    await _one_turn(session, ws)

    assert "It costs five pounds (or so" in _heard(trace)
    assert _shown(ws) == "It costs five pounds (or so"
    await session._stop_call()


async def test_a_sigh_left_with_no_words_is_not_sent_to_her_voice() -> None:
    ws, trace, seen = FakeWS(), [], []
    session = _session(ws, trace, seen, ["That's a shame. ", "[sigh]"])
    await start_call(session, ws)
    await _one_turn(session, ws)

    assert "That's a shame." in _heard(trace)
    assert "[sigh]" not in _heard(trace)
    assert session.history[-1]["content"] == "That's a shame."
    await session._stop_call()


async def test_a_laugh_waits_for_the_rest_of_its_sentence_before_it_is_said() -> None:
    # Its opening clause is long enough to be said alone; the sympathy comes after it.
    ws, trace, seen = FakeWS(), [], []
    reply = ["[laugh] You poor thing, ", "that's the worst. ", "Oh no, that's awful. [laugh]"]
    session = _session(ws, trace, seen, reply)
    await start_call(session, ws)
    await _one_turn(session, ws)

    assert "[laugh]" not in _heard(trace)
    assert "You poor thing, that's the worst." in _heard(trace)
    await session._stop_call()


async def test_cut_off_while_she_laughs_she_said_no_words_yet() -> None:
    # The laugh takes its share of the chunk's audio: a third in, no word was heard.
    ws, trace, seen = FakeWS(), [], []
    session = _session(ws, trace, seen, ["[laugh] Okay, that one got me."])
    await start_call(session, ws)
    await _one_turn(session, ws)

    total = session.ledger.total_samples
    assert session.ledger.text_up_to(total // 3) == ""
    assert session.ledger.text_up_to(total) == "Okay, that one got me."
    await session._stop_call()
