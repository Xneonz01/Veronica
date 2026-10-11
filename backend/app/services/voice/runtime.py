"""Wiring for the voice stack: GPU manager + adapters + voice registry.

Building the runtime is cheap — the adapter constructors only store references,
and the heavy ML imports/model loads happen lazily through the GPU manager on
first use. So this can run in the app lifespan without slowing startup. Her voice
clips' key is opened here, and the first start that has one seals the clips
(`clips.py`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.adapters.base import TurnArbiter
from app.adapters.llm.ollama_client import OllamaLLMClient
from app.adapters.stt.faster_whisper_stream import (
    STT_MODEL_ID,
    FasterWhisperStreamSTT,
    whisper_loader,
)
from app.adapters.tts.chatterbox_stream import (
    TTS_MODEL_ID,
    ChatterboxStreamTTS,
    chatterbox_loader,
)
from app.adapters.turn.smart_turn import open_smart_turn
from app.core.config import Settings
from app.core.event_bus import EventBus
from app.core.gpu_manager import CudaGpuModelManager, GpuMode, gib
from app.services.voice.active_voice import KeptVoice, starting_voice
from app.services.voice.calls import LiveCalls
from app.services.voice.clips import Clips
from app.services.voice.voice_studio import VoiceStudio
from app.services.voice.voices import VoiceRegistry, VoiceService

logger = logging.getLogger(__name__)

_STT_VRAM_GB = 2.0
_TTS_VRAM_GB = 3.0


@dataclass(slots=True)
class VoiceRuntime:
    """The constructed voice components, shared via ``app.state``."""

    manager: CudaGpuModelManager
    stt: FasterWhisperStreamSTT
    tts: ChatterboxStreamTTS
    llm: OllamaLLMClient
    voice_service: VoiceService
    studio: VoiceStudio | None = None  # set in production; None in fake test runtimes
    # voices.json couldn't be read at start, and was set aside (`VoiceRegistry._load`);
    # stuck when it couldn't be moved, or the voices folder can't be used.
    voices_damaged: bool = False
    voices_stuck: bool = False
    # Why her clips' key can't be used here (`Clips.locked`); None when it can. And
    # whether a recovery key can open it.
    voices_locked: str | None = None
    voices_recoverable: bool = False
    # The calls on this process, and the release of the speech models after the last.
    calls: LiveCalls = field(init=False)
    _arbiter: TurnArbiter | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.calls = LiveCalls(self.manager)

    async def turn_arbiter(self) -> TurnArbiter | None:
        """smart-turn, opened on the first call and shared by the calls after it.

        None if it can't be opened: the call still works, and each turn waits out
        `vad.end_silence_max_ms`. It is tried again on the next call.
        """
        if self._arbiter is None:
            try:
                self._arbiter = await asyncio.to_thread(open_smart_turn)
            except Exception:
                logger.warning(
                    "smart-turn couldn't be opened, so each turn waits the longest pause",
                    exc_info=True,
                )
        return self._arbiter


def clip_transcriber(
    read_clip: Callable[[str], bytes], transcribe_clip: Callable[[bytes], Awaitable[str]]
) -> Callable[[str], Awaitable[str]]:
    """What registering a voice transcribes, for an engine that needs a transcript: its
    clip opened, since clips are sealed at rest (ADR 0001), and by its path the sealed
    file was read, not the sound."""

    async def transcribe(path: str) -> str:
        return await transcribe_clip(await asyncio.to_thread(read_clip, path))

    return transcribe


async def build_voice_runtime(settings: Settings, bus: EventBus) -> VoiceRuntime:
    """Construct the GPU manager, register model loaders, and build the adapters. Her
    clips are opened off the event loop: their key's lock may wait on another start."""
    manager = CudaGpuModelManager(budget_gib=gib(settings.gpu.vram_budget_gb))
    manager.register(
        STT_MODEL_ID, whisper_loader(settings.asr), {GpuMode.conversation}, gib(_STT_VRAM_GB)
    )
    manager.register(
        TTS_MODEL_ID, chatterbox_loader(settings.tts), {GpuMode.conversation}, gib(_TTS_VRAM_GB)
    )

    registry = VoiceRegistry(settings.voice.voices_dir)
    clips = await asyncio.to_thread(Clips.open, registry.directory)
    kept = KeptVoice(registry.directory)
    default = settings.voice.active_voice_id
    active = await asyncio.to_thread(lambda: starting_voice(kept.read(), registry, default))
    stt = FasterWhisperStreamSTT(manager, settings.asr)
    tts = ChatterboxStreamTTS(manager, settings.tts, registry, read_clip=clips.read)
    llm = OllamaLLMClient(settings.llm)
    voice_service = VoiceService(
        registry,
        tts,
        bus,
        active,
        transcriber=clip_transcriber(clips.read, stt.transcribe_clip),
        needs_prompt_text=tts.needs_prompt_text,
        kept=kept,
    )
    studio = VoiceStudio(registry, tts, voice_service, bus, default, clips=clips)
    return VoiceRuntime(
        manager=manager,
        stt=stt,
        tts=tts,
        llm=llm,
        voice_service=voice_service,
        studio=studio,
        voices_damaged=registry.damaged,
        voices_stuck=registry.stuck,
        voices_locked=clips.locked,
        voices_recoverable=clips.recoverable,
    )
