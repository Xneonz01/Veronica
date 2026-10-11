"""Drive a real voice call and time each stage of every turn.

    uv run python -m scripts.call_probe --label baseline --out probe.json
    uv run python -m scripts.call_probe --report probe-1.json probe-2.json

Connects to a running backend the way the app does (`/ws/voice`), speaks recorded
questions into the call as 20 ms microphone frames in real time, and keeps the
metrics row the server sends for each turn. It adds one number of its own: from the
last question frame sent to the first reply audio received. Between questions it
streams silence, as an open microphone does, and like the app it plays each reply in
real time, reports how far it has got, and lets the reply finish before asking again
(`probe_playback`).

The call is real, so the backend summarises it and learns from it afterwards. Point
the probe at a backend running on a scratch data folder, not at your own memories.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from app.core.config import get_settings
from scripts import probe_audio
from scripts.probe_playback import Speaker
from scripts.probe_stats import ProbeRun, ProbeTurn, TurnRow, report

# Everyday questions that need no tool, so every turn is timed the same way.
QUESTIONS = (
    "What's the capital of France?",
    "How many minutes are in a day?",
    "Give me one quick tip for sleeping better.",
    "Why is the sky blue?",
    "What's a good name for a grey cat?",
    "Tell me a fun fact about octopuses.",
    "How do I boil an egg?",
    "What's the tallest mountain in the world?",
)
_FRAME_S = probe_audio.FRAME_SAMPLES / probe_audio.MIC_RATE
_SILENCE = bytes(probe_audio.FRAME_BYTES)
_WARMUP_TIMEOUT_S = 300.0  # a cold start loads every model
_PAUSE_S = 1.0  # between an answer and the next question, as a person would pause
_REPORT_EVERY = 5  # frames: the app reports its playback position every 100 ms


class ProbeError(RuntimeError):
    """The call could not be measured; the message says why."""


class Call:
    """One call on `/ws/voice`: what the probe sends, and what comes back."""

    def __init__(self, socket: ClientConnection) -> None:
        self._socket = socket
        self._pending: deque[bytes] = deque()
        self._streaming = True
        self.state = ""
        self.rows: list[dict[str, Any]] = []
        self.finals: list[str] = []
        self.errors: list[str] = []
        self.closed = False
        self.question_sent_at: float | None = None
        self.reply_heard_at: float | None = None
        self.speaker = Speaker()
        self._reported: tuple[int, int] | None = None

    async def receive(self) -> None:
        """Record what the server sends until the call closes."""
        try:
            async for message in self._socket:
                if isinstance(message, bytes):
                    if self.question_sent_at is not None and self.reply_heard_at is None:
                        self.reply_heard_at = time.perf_counter()
                    self.speaker.receive(len(message) // 2, time.perf_counter())
                    continue
                try:
                    self._on_event(json.loads(message))
                except ValueError:
                    self.errors.append("the server sent a message that isn't JSON")
        except ConnectionClosed:
            pass  # reported by `until`, which is what's waiting on the call
        finally:
            self.closed = True

    def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "veronica_state":
            self.state = str(event.get("state"))
        elif kind == "asr_final":
            self.finals.append(str(event.get("text")))
        elif kind == "metrics":
            self.rows.append(event)
        elif kind == "error":
            self.errors.append(f"the server reported {event.get('kind')}")
        elif kind == "reply_audio" and isinstance(event.get("reply"), int):
            self.speaker.begin(event["reply"])

    async def stream_microphone(self) -> None:
        """Send a frame every 20 ms: question audio while there is some, else silence."""
        next_at = time.perf_counter()
        frame = 0
        try:
            while self._streaming:
                if self._pending:
                    await self._socket.send(self._pending.popleft())
                    if not self._pending:
                        self.question_sent_at = time.perf_counter()
                else:
                    await self._socket.send(_SILENCE)
                frame += 1
                if frame % _REPORT_EVERY == 0:
                    await self._report_playback()
                # Paced against a deadline: a plain sleep overruns (by up to 15.6 ms on
                # Windows), and the drift would stretch the audio out.
                next_at += _FRAME_S
                await asyncio.sleep(max(0.0, next_at - time.perf_counter()))
        except ConnectionClosed:
            return  # the receiver sees the same closure, and `until` reports it

    async def _report_playback(self) -> None:
        """Send the reply's playback position when it has moved, as the app does."""
        reply = self.speaker.reply
        if reply is None:
            return
        position = (reply, self.speaker.played(time.perf_counter()))
        if position != self._reported:
            self._reported = position
            await self._socket.send(
                json.dumps({"action": "playback_position", "reply": reply, "samples": position[1]})
            )

    def mute(self) -> None:
        """Stop the microphone after the frame in flight (a send isn't cancelled)."""
        self._streaming = False

    async def until(self, done: Callable[[], bool], timeout_s: float) -> None:
        """Wait for `done`; stop early on a server error or a closed call."""
        deadline = time.perf_counter() + timeout_s
        while not done():
            if self.errors:
                raise ProbeError(self.errors[-1])
            if self.closed:
                raise ProbeError("the server closed the call")
            if time.perf_counter() > deadline:
                raise ProbeError(f"no answer in {timeout_s:.0f} s (state: {self.state or '?'})")
            await asyncio.sleep(0.02)

    async def ask(self, question: str, frames: list[bytes], timeout_s: float) -> ProbeTurn:
        """Speak one question and wait for the whole answer and its metrics row.

        The question must come back as exactly one transcript and one uninterrupted
        row. A pause inside the recording splits it into two utterances, the second
        interrupting the reply to the first; counting such a turn would pin one
        question's timings on another.
        """
        rows, finals = len(self.rows), len(self.finals)
        self.question_sent_at = self.reply_heard_at = None
        self._pending.extend(frames)
        await self.until(lambda: self.question_sent_at is not None, timeout_s)
        await self.until(lambda: len(self.rows) > rows, timeout_s)
        await self.until(lambda: self.state == "listening", timeout_s)
        # Heard to the end, as a person would hear it, before the next question.
        await self.until(lambda: self.speaker.finished(time.perf_counter()), timeout_s)
        new_rows, heard = self.rows[rows:], self.finals[finals:]
        if len(heard) != 1:
            raise ProbeError(
                f"{question!r} was heard as {len(heard)} utterances; its recording "
                "probably has a pause in it (delete its clip to record it again)"
            )
        if len(new_rows) != 1 or new_rows[0].get("barged_in"):
            raise ProbeError(f"the reply to {question!r} was interrupted or reported twice")
        client_ms = None
        if self.question_sent_at is not None and self.reply_heard_at is not None:
            client_ms = round((self.reply_heard_at - self.question_sent_at) * 1000)
        return ProbeTurn(
            question=question,
            heard=heard[0],
            row=TurnRow.model_validate(new_rows[0]),
            client_first_audio_ms=client_ms,
        )


async def run_call(url: str, turns: int, audio_dir: Path, timeout_s: float) -> list[ProbeTurn]:
    """Ask `turns` questions in one call and return what each turn measured."""
    questions = [QUESTIONS[index % len(QUESTIONS)] for index in range(turns)]
    clips = {
        # The first recording pays the TTS model's cold load, so it gets the warm-up allowance.
        text: await probe_audio.question_frames(url, text, audio_dir, timeout_s=_WARMUP_TIMEOUT_S)
        for text in dict.fromkeys(questions)
    }
    async with connect(f"{url}/ws/voice", proxy=None, max_size=None) as socket:
        call = Call(socket)
        receiver = asyncio.create_task(call.receive())
        await socket.send(json.dumps({"action": "call_start", "greeting": {"enabled": False}}))
        await call.until(lambda: call.state == "listening", _WARMUP_TIMEOUT_S)
        microphone = asyncio.create_task(call.stream_microphone())
        results: list[ProbeTurn] = []
        try:
            for number, text in enumerate(questions, start=1):
                try:
                    turn = await call.ask(text, clips[text], timeout_s)
                except ProbeError as exc:
                    raise ProbeError(f"question {number} of {len(questions)}: {exc}") from exc
                results.append(turn)
                perceived = turn.row.perceived_first_audio_ms
                print(f"  {number}. heard {turn.heard!r}; first audio {perceived} ms after it")
                await asyncio.sleep(_PAUSE_S)
        finally:
            call.mute()
            await microphone
        await socket.send(json.dumps({"action": "call_stop"}))
        await socket.close()
        await receiver
    return results


def reason(exc: BaseException) -> str:
    """What went wrong, even for an exception raised without a message."""
    return str(exc) or type(exc).__name__


def _diagnostics(url: str) -> dict[str, Any]:
    """The backend's `/diagnostics`, for its version and the call's loop lag."""
    http = url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback only
    with opener.open(f"{http}/diagnostics", timeout=10) as response:
        data: dict[str, Any] = json.load(response)
    return data


def _probe(options: argparse.Namespace) -> int:
    started_at = datetime.now(UTC).isoformat()
    # Spoken in her active voice, which may be a clone: kept with her other data.
    audio_dir = options.audio_dir or get_settings().data_path() / "probes"
    print(f"Probing {options.url} with {options.turns} questions...")
    try:
        turns = asyncio.run(run_call(options.url, options.turns, audio_dir, options.turn_timeout))
    except (ProbeError, OSError, TimeoutError, ValueError, WebSocketException) as exc:
        print(f"Probe failed: {reason(exc)}", file=sys.stderr)
        return 1
    try:
        diagnostics = _diagnostics(options.url)
    except OSError as exc:  # the turns are still worth keeping without it
        print(f"No diagnostics ({exc}); saving the run without version or loop lag.")
        diagnostics = {}
    run = ProbeRun(
        label=options.label,
        version=diagnostics.get("version"),
        started_at=started_at,
        turns=turns,
        loop_lag=diagnostics.get("loop_lag"),
    )
    if options.out is not None:
        options.out.parent.mkdir(parents=True, exist_ok=True)
        options.out.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    print("\n".join(["", *report([run])]))
    return 0


def main() -> int:
    """Probe a running backend, or summarise saved runs with `--report`."""
    parser = argparse.ArgumentParser(description="Time every stage of live voice turns.")
    parser.add_argument("--url", default="ws://127.0.0.1:8000", help="the backend's socket URL")
    parser.add_argument("--turns", type=int, default=8, help="questions to ask in the call")
    parser.add_argument("--label", default="probe", help="name for this configuration")
    parser.add_argument("--out", type=Path, help="save the run as JSON here")
    parser.add_argument("--audio-dir", type=Path, help="question cache (default: data_dir/probes)")
    parser.add_argument("--turn-timeout", type=float, default=120.0, help="seconds per answer")
    parser.add_argument("--report", type=Path, nargs="+", help="summarise saved runs instead")
    options = parser.parse_args()
    if options.turns < 1:
        parser.error("--turns must be at least 1")
    if options.report:
        runs = [ProbeRun.model_validate_json(path.read_text("utf-8")) for path in options.report]
        print("\n".join(report(runs)))
        return 0
    return _probe(options)


if __name__ == "__main__":
    sys.exit(main())
