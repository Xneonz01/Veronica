"""Push-to-talk debugging harness: STT and text->TTS over one WebSocket.

Served at `/ws/voice/harness`. It predates the live call (`RealtimeSession`) and is
kept for checking mic->STT and text->TTS->speakers in isolation, with no VAD, LLM or
barge-in. Control messages are JSON ({"action": "start"|"stop"|"speak"}); inbound
audio is binary 16 kHz PCM16; transcripts go out as JSON and TTS audio as binary
24 kHz PCM16.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Protocol

from fastapi import WebSocket, WebSocketDisconnect

from app.services.voice.audio import Transcript

logger = logging.getLogger(__name__)


class _Transcriber(Protocol):
    def stream_transcripts(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]: ...


class _Synthesizer(Protocol):
    def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]: ...


class HarnessSession:
    """Drives one harness WebSocket connection."""

    def __init__(
        self,
        websocket: WebSocket,
        transcriber: _Transcriber,
        synthesizer: _Synthesizer,
        voice_id: str,
    ) -> None:
        self._ws = websocket
        self._stt = transcriber
        self._tts = synthesizer
        self._voice_id = voice_id
        self._queue: asyncio.Queue[bytes | None] | None = None
        self._stt_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        try:
            while True:
                message = await self._ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    self._on_audio(data)
                    continue
                text = message.get("text")
                if text is not None:
                    await self._on_control(json.loads(text))
        except WebSocketDisconnect:
            pass
        finally:
            await self._cancel_turn()

    async def _on_control(self, message: dict[str, str]) -> None:
        action = message.get("action")
        if action == "start":
            await self._begin_turn()
        elif action == "stop":
            await self._end_turn()
        elif action == "speak":
            await self._speak(message.get("text", ""))

    async def _begin_turn(self) -> None:
        await self._cancel_turn()
        self._queue = asyncio.Queue()
        self._stt_task = asyncio.create_task(self._run_stt(self._queue))

    async def _end_turn(self) -> None:
        if self._queue is not None:
            await self._queue.put(None)  # sentinel -> finalize this utterance
        if self._stt_task is not None:
            await self._stt_task  # block until the final transcript is sent
            self._stt_task = None
        self._queue = None

    def _on_audio(self, frame: bytes) -> None:
        if self._queue is not None:
            self._queue.put_nowait(frame)

    async def _run_stt(self, queue: asyncio.Queue[bytes | None]) -> None:
        async def frames() -> AsyncIterator[bytes]:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        async for transcript in self._stt.stream_transcripts(frames()):
            kind = "asr_final" if transcript.is_final else "asr_partial"
            await self._ws.send_text(json.dumps({"type": kind, "text": transcript.text}))

    async def _speak(self, text: str) -> None:
        async for pcm in self._tts.synthesize_stream(text, self._voice_id):
            await self._ws.send_bytes(pcm)
        await self._ws.send_text(json.dumps({"type": "tts_done"}))

    async def _cancel_turn(self) -> None:
        if self._stt_task is not None:
            self._stt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stt_task
            self._stt_task = None
        self._queue = None
