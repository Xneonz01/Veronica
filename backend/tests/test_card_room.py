"""A call's room on the graphics card (bug 46): the card's own figure against what the call
would still add, her backend's peak less what it holds now and her language model's files
when Ollama hasn't loaded it; a card too small for a call told apart; judged by what was
measured, and never a guess, nor a failed call, when it can't be read."""

from __future__ import annotations

import os
import threading
from typing import Any

import pytest

from app.adapters.llm import ollama_models
from app.adapters.llm.ollama_errors import OllamaUnreachableError
from app.adapters.llm.ollama_models import InstalledModel
from app.core import gpu_process, machine
from app.core.config import Settings
from app.core.config_llm import LlmSettings
from app.services.voice.realtime import card_room
from app.services.voice.realtime.card_room import (
    BACKEND_MIB,
    KEEP_FREE_MIB,
    TIGHT_MIB,
    HerModel,
    Room,
)

pytestmark = pytest.mark.card_room

CARD = 16_376  # the development machine's card, as nvidia-smi gives it
FILES = 6_288  # qwen3.5:9b's files


@pytest.mark.parametrize(
    ("left", "verdict"),
    [
        (-224, "full"),  # measured: no answer after the first
        (KEEP_FREE_MIB - 1, "full"),  # 246 measured the same
        (KEEP_FREE_MIB, "tight"),
        (TIGHT_MIB - 1, "tight"),  # 504 measured: 1.5 times slower
        (TIGHT_MIB, "fits"),
    ],
)
def test_the_verdict_is_what_would_be_left_beside_the_call(left: int, verdict: str) -> None:
    room = Room(total_mib=CARD, free_mib=4_000 + left, adds_mib=4_000, takes_mib=13_083)
    assert room.verdict == verdict
    assert room.needs_mib == 4_000 + KEEP_FREE_MIB  # what must be free to start


def test_a_card_that_couldnt_hold_the_call_alone_is_small_not_full() -> None:
    takes = BACKEND_MIB + FILES
    room = Room(total_mib=takes + KEEP_FREE_MIB, free_mib=0, adds_mib=takes, takes_mib=takes)
    assert room.verdict == "full"  # room enough once another program lets go
    small = Room(total_mib=12_288, free_mib=9_900, adds_mib=11_738, takes_mib=takes)
    assert small.card_needs_mib == takes + KEEP_FREE_MIB
    assert small.verdict == "small"  # whatever is free now


@pytest.fixture
def card(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The readings `measure` takes, as told; what it asked is kept."""
    told: dict[str, Any] = {
        "card": machine.CardUse(used_mib=11_838, total_mib=CARD),
        "held": 2_033,
        "model": HerModel(FILES, loaded=True),
        "asked_pid": None,
    }

    def held_mib(pid: int) -> int | None:
        told["asked_pid"] = pid
        held: int | None = told["held"]
        return held

    monkeypatch.setattr(machine, "card_use", lambda: told["card"])
    monkeypatch.setattr(gpu_process, "held_mib", held_mib)
    monkeypatch.setattr(card_room, "her_model", lambda _llm: told["model"])
    return told


async def test_the_room_is_the_cards_figure_against_what_the_call_would_add(
    card: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[tuple[str, str, dict[str, Any]]] = []

    class Box:
        def record(self, level: str, event: str, **fields: Any) -> None:
            recorded.append((level, event, fields))

    monkeypatch.setattr(card_room, "current_blackbox", Box)
    # As measured with 1.5 GiB held by another program and her model loaded.
    room = await card_room.measure(Settings())

    assert room == Room(
        total_mib=CARD,
        free_mib=CARD - 11_838,
        adds_mib=BACKEND_MIB - 2_033,  # her model is loaded: in the card's figure
        takes_mib=BACKEND_MIB + FILES,
    )
    assert room.verdict == "full"
    assert card["asked_pid"] == os.getpid()  # her own share, not another process's
    fields = {"free_mb": 4_538, "adds_mb": BACKEND_MIB - 2_033, "takes_mb": BACKEND_MIB + FILES}
    shares = {"held_mb": 2_033, "model_mb": FILES, "verdict": "full"}
    assert recorded == [("info", "call.card_room", {**fields, **shares})]


async def test_her_model_still_to_load_is_added_and_a_loaded_backend_adds_nothing(
    card: dict[str, Any],
) -> None:
    card["card"] = machine.CardUse(used_mib=2_680, total_mib=CARD)  # a fresh start
    card["held"], card["model"] = 1_345, HerModel(FILES, loaded=False)
    room = await card_room.measure(Settings())
    assert room is not None
    assert room.adds_mib == BACKEND_MIB - 1_345 + FILES
    assert room.verdict == "fits"

    card["held"] = BACKEND_MIB + 100  # the speech models still loaded from a last call
    room = await card_room.measure(Settings())
    assert room is not None
    assert (room.adds_mib, room.takes_mib) == (FILES, BACKEND_MIB + 100 + FILES)


async def test_a_card_too_small_for_a_call_says_so_with_or_without_her_model(
    card: dict[str, Any],
) -> None:
    card["held"] = 1_345
    for used, loaded in ((2_300, False), (9_000, True)):  # 12 GB, nothing else running
        card["card"] = machine.CardUse(used_mib=used, total_mib=12_288)
        card["model"] = HerModel(FILES, loaded=loaded)
        room = await card_room.measure(Settings())
        assert room is not None and room.verdict == "small"


@pytest.mark.parametrize(
    "unread",
    [
        {"card": None},  # no NVIDIA card, or more than one
        {"held": None},  # no count of it off Windows
    ],
)
async def test_a_room_that_cant_be_read_isnt_judged(
    card: dict[str, Any], unread: dict[str, Any]
) -> None:
    card.update(unread)
    assert await card_room.measure(Settings()) is None


@pytest.mark.parametrize("setting", ["asr", "tts", "memory"])
async def test_a_model_set_to_run_off_the_card_leaves_the_figures_without_meaning(
    card: dict[str, Any], setting: str
) -> None:
    settings = Settings()
    if setting == "memory":
        settings.memory.embedding_device = "cpu"
    else:
        getattr(settings, setting).device = "cpu"
    assert await card_room.measure(settings) is None
    settings.asr.device = settings.tts.device = settings.memory.embedding_device = "cuda:0"
    assert await card_room.measure(settings) is not None  # a card named by its number


@pytest.mark.parametrize(
    "error",
    [
        machine.ReadingError("nvidia-smi exited with 15"),
        OSError("pdh.dll couldn't be loaded"),  # a check that breaks doesn't fail the call
    ],
)
async def test_a_reading_that_fails_isnt_judged(
    card: dict[str, Any], monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def broken() -> None:
        raise error

    monkeypatch.setattr(machine, "card_use", broken)
    assert await card_room.measure(Settings()) is None


async def test_a_reading_that_takes_too_long_isnt_waited_for(
    card: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    released = threading.Event()

    def hung() -> machine.CardUse:
        released.wait(5)
        return machine.CardUse(used_mib=0, total_mib=CARD)  # judged, had it been waited for

    monkeypatch.setattr(machine, "card_use", hung)
    monkeypatch.setattr(card_room, "_READ_TIMEOUT_S", 0.05)
    try:
        assert await card_room.measure(Settings()) is None
    finally:
        released.set()


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Ollama's lists as told."""
    told: dict[str, Any] = {
        "loaded": [],
        "installed": [InstalledModel("qwen3.5:9b", None, FILES)],
    }

    def listed(key: str) -> Any:
        def answer(_llm: LlmSettings) -> Any:
            if isinstance(told[key], Exception):
                raise told[key]
            return told[key]

        return answer

    monkeypatch.setattr(ollama_models, "loaded_models", listed("loaded"))
    monkeypatch.setattr(ollama_models, "installed_models", listed("installed"))
    return told


def test_her_model_is_its_files_and_whether_ollama_has_it_loaded(ollama: dict[str, Any]) -> None:
    llm = LlmSettings(model="Qwen3.5:9B")  # compared as Ollama compares names
    assert card_room.her_model(llm) == HerModel(FILES, loaded=False)
    ollama["loaded"] = ["qwen3:8b"]  # another model loaded isn't hers
    assert card_room.her_model(llm) == HerModel(FILES, loaded=False)
    ollama["loaded"] = ["qwen3.5:9b"]
    assert card_room.her_model(llm) == HerModel(FILES, loaded=True)


@pytest.mark.parametrize(
    "told",
    [
        {"installed": []},  # not pulled: nothing will load
        {"installed": [InstalledModel("qwen3.5:9b", None)]},  # its size isn't said
        {"loaded": OllamaUnreachableError("no model list")},  # nothing will load either
        {"installed": OllamaUnreachableError("no model list")},
    ],
)
def test_a_model_that_wont_load_takes_nothing(ollama: dict[str, Any], told: dict[str, Any]) -> None:
    ollama.update(told)
    assert card_room.her_model(LlmSettings()).files_mib == 0


def test_a_model_on_another_computer_takes_nothing_here(ollama: dict[str, Any]) -> None:
    ollama["loaded"] = AssertionError("another computer's Ollama isn't asked")
    here = card_room.her_model(LlmSettings(base_url="http://192.168.1.20:11434"))
    assert here == HerModel(0, loaded=False)
