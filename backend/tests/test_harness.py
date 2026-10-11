"""Push-to-talk voice harness: streamed STT transcripts and text-to-TTS echo (fakes)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.services.voice.audio import Transcript
from app.services.voice.harness import HarnessSession


class _FakeWS:
    def __init__(self, incoming: list[dict[str, object]]) -> None:
        self._incoming = incoming
        self._i = 0
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []

    async def receive(self) -> dict[str, object]:
        if self._i >= len(self._incoming):
            return {"type": "websocket.disconnect"}
        message = self._incoming[self._i]
        self._i += 1
        return message

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


class _FakeStt:
    def stream_transcripts(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        async def gen() -> AsyncIterator[Transcript]:
            count = 0
            async for _ in audio:
                count += 1
                yield Transcript(f"partial {count}", is_final=False)
            yield Transcript("final transcript", is_final=True)

        return gen()


class _FakeTts:
    def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for word in text.split():
                yield word.encode()

        return gen()


def _ctrl(action: str, **extra: str) -> dict[str, object]:
    return {"type": "websocket.receive", "text": json.dumps({"action": action, **extra})}


def _audio(data: bytes) -> dict[str, object]:
    return {"type": "websocket.receive", "bytes": data}


async def test_push_to_talk_streams_partials_and_final() -> None:
    ws = _FakeWS([_ctrl("start"), _audio(b"aa"), _audio(b"bb"), _ctrl("stop")])
    session = HarnessSession(ws, _FakeStt(), _FakeTts(), "veronica-default")
    await session.run()

    kinds = [json.loads(t) for t in ws.sent_text]
    partials = [m["text"] for m in kinds if m["type"] == "asr_partial"]
    finals = [m["text"] for m in kinds if m["type"] == "asr_final"]
    assert partials == ["partial 1", "partial 2"]
    assert finals == ["final transcript"]


async def test_speak_streams_tts_audio_then_done() -> None:
    ws = _FakeWS([_ctrl("speak", text="hello there")])
    session = HarnessSession(ws, _FakeStt(), _FakeTts(), "veronica-default")
    await session.run()

    assert ws.sent_bytes == [b"hello", b"there"]
    assert json.loads(ws.sent_text[-1])["type"] == "tts_done"
