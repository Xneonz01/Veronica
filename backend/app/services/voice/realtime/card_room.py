"""Whether the graphics card has room for a call, judged before the call loads anything
(bug 46).

Nothing else stops a call the card can't hold. On Windows, CUDA memory asked for past
what the card has spills into system memory instead of failing, so her models are paged
in and out while she speaks, and the call stops answering. Neither the GPU manager nor
Ollama sees it coming: a process's own reading there leaves out what another program
holds idle, and Ollama loaded her whole model with 4 GB of the card free (measured with
10 GiB held by another process).

So the room is judged from the card's own figure, every program's share in it
(`machine.card_use`), against what the call would still add: her backend's share at a
call's peak less what it holds now (`gpu_process.held_mib`, Windows' count, so the models
still loaded from a last call, and the cache it left, count for what they are), and her
language model's files when Ollama hasn't loaded it, the least it takes (qwen3.5:9b: 6,288
MiB of files, 6,718 on the card). Once loaded, it is in the card's figure, with whatever
another program holds. A card that couldn't hold the call with nothing else on it is told
apart, since no program closed would make room there.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Literal

from app.adapters.llm import ollama_models
from app.adapters.llm.ollama_errors import OllamaUnreachableError
from app.core import gpu_process, machine
from app.core.config import Settings
from app.core.config_llm import LlmSettings
from app.telemetry import current_blackbox

logger = logging.getLogger(__name__)

# Her backend's share of the card at a call's peak, by Windows' count, with the default
# models on the card (large-v3-turbo, Chatterbox Turbo, e5-base): the most in four calls on
# 0.34.5, two of eight turns (cold and warm) and two of four, 6,741 to 6,795 MiB.
BACKEND_MIB = 6_795
# What the card must keep free beside a call. Measured on 0.34.5 with another process
# holding memory idle beside calls of four turns, her model loaded: with 246 MiB left over
# a call stopped answering after its first; with 504 it answered, its first words 1.5
# times later (3.7 s); with 574, as quickly as without (2.7 s).
KEEP_FREE_MIB = 384
# Less left over than this and the call starts with a word that she may be slow.
TIGHT_MIB = 1_024
# nvidia-smi answers in about 50 ms, Windows' count in under one, Ollama's lists in a few:
# a reading that takes longer is a driver or a server in trouble, not worth a call's wait.
_READ_TIMEOUT_S = 3.0

Verdict = Literal["fits", "tight", "full", "small"]


@dataclass(frozen=True, slots=True)
class Room:
    """The card's memory, in all and free now, and what a call takes of it, in MiB."""

    total_mib: int
    free_mib: int
    adds_mib: int  # what the call would still put on the card
    takes_mib: int  # what it takes of a card with nothing else on it, at the least

    @property
    def needs_mib(self) -> int:
        """What must be free for the call to start: what it adds, and the room kept beside."""
        return self.adds_mib + KEEP_FREE_MIB

    @property
    def card_needs_mib(self) -> int:
        """What the card must have in all, with nothing else on it."""
        return self.takes_mib + KEEP_FREE_MIB

    @property
    def verdict(self) -> Verdict:
        if self.card_needs_mib > self.total_mib:
            return "small"  # no program closed would make room
        left = self.free_mib - self.adds_mib
        if left < KEEP_FREE_MIB:
            return "full"
        if left < TIGHT_MIB:
            return "tight"
        return "fits"


@dataclass(frozen=True, slots=True)
class HerModel:
    """Her language model as this card sees it: its files (0 when it isn't on this card or
    nothing can be known of it) and whether Ollama has it loaded."""

    files_mib: int
    loaded: bool


async def measure(settings: Settings) -> Room | None:
    """The room a call has now, or None when it can't be judged: a model set to run off
    the card (the figures are for all of them on it), no NVIDIA card or more than one, a
    system without Windows' count, or a reading that failed or took too long. A call that
    can't be judged goes ahead, as before."""
    if not _all_on_the_card(settings):
        return None
    try:
        card, held, model = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(machine.card_use),
                asyncio.to_thread(gpu_process.held_mib, os.getpid()),
                asyncio.to_thread(her_model, settings.llm),
            ),
            _READ_TIMEOUT_S,
        )
    except machine.ReadingError as error:
        logger.warning("the card's room for a call couldn't be read: %s", error)
        return None
    except TimeoutError:
        logger.warning("the card's room for a call wasn't read in %g s", _READ_TIMEOUT_S)
        return None
    except Exception:  # a check that breaks must not take the call with it
        logger.warning("the card's room for a call couldn't be judged", exc_info=True)
        return None
    if card is None or held is None:
        return None
    room = Room(
        total_mib=card.total_mib,
        free_mib=max(0, card.total_mib - card.used_mib),
        adds_mib=max(0, BACKEND_MIB - held) + (0 if model.loaded else model.files_mib),
        takes_mib=max(held, BACKEND_MIB) + model.files_mib,
    )
    current_blackbox().record(
        "info",
        "call.card_room",
        free_mb=room.free_mib,
        adds_mb=room.adds_mib,
        takes_mb=room.takes_mib,
        held_mb=held,
        model_mb=model.files_mib,
        verdict=room.verdict,
    )
    return room


async def fits_her_model(settings: Settings) -> bool:
    """Whether the card has room to load her language model now, beside the memory model
    the start is loading too, as the same figures judge a call's room. Loaded already, or a
    room that can't be judged, it goes ahead: the first message would load it anyway."""
    if not _all_on_the_card(settings):
        return True
    try:
        card, model = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(machine.card_use), asyncio.to_thread(her_model, settings.llm)
            ),
            _READ_TIMEOUT_S,
        )
    except (machine.ReadingError, TimeoutError):  # unknown, as a call's check takes it
        logger.warning("the card's room for her model couldn't be read")
        return True
    except Exception:  # a check that breaks must not stop the start
        logger.warning("the card's room for her model couldn't be judged", exc_info=True)
        return True
    if card is None or model.loaded:
        return True
    memory_mib = int(settings.memory.embedding_vram_gb * 1024)
    return card.total_mib - card.used_mib >= model.files_mib + memory_mib + KEEP_FREE_MIB


def her_model(llm: LlmSettings) -> HerModel:
    """Her language model's files and whether Ollama has it loaded. Nothing of it is on this
    card when Ollama runs elsewhere, and nothing will load when it isn't answering or
    hasn't got the model (the call's first turn then says why)."""
    if not ollama_models.on_this_computer(llm):
        return HerModel(0, loaded=False)
    try:
        loaded = any(
            ollama_models.same_model(name, llm.model) for name in ollama_models.loaded_models(llm)
        )
        found = ollama_models.find(ollama_models.installed_models(llm), llm.model)
    except OllamaUnreachableError:
        return HerModel(0, loaded=False)
    files = found.size_mib if found is not None and found.size_mib is not None else 0
    return HerModel(files, loaded)


def _all_on_the_card(settings: Settings) -> bool:
    devices = (settings.asr.device, settings.tts.device, settings.memory.embedding_device)
    return all(device.partition(":")[0] == "cuda" for device in devices)
