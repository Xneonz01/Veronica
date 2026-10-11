"""What this computer has, against what Veronica was measured using: "This computer" in
Settings > About, and `scripts/diagnostics.py --doctor`.

Five checks, each a verdict and the values its words need (the app has the words, as for
`/health`'s problems): the graphics card, the memory (RAM), the disk her memory file is
on, Ollama with her language model, and the model files. Only a measured figure or a
hard limit decides a verdict: a card or memory smaller than calls were measured using
is flagged, and so is a disk without room for the copy an update keeps first
(`upgrade_copy.room_needed`). What has no figure isn't judged.

It only looks: nothing is created, loaded or downloaded, and a check that can't be read,
or doesn't answer in time, says so without failing the others.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

from app.adapters import model_files
from app.adapters.llm import ollama_models
from app.adapters.llm.ollama_errors import OllamaUnreachableError
from app.core import machine
from app.core.config import Settings
from app.paths import anchored
from app.services.memory import upgrade_copy
from app.telemetry import current_blackbox

logger = logging.getLogger(__name__)

# Measured on 0.26.0 (2026-09-26) with the default settings (qwen3.5:9b with a context of
# 8,192) on a 16 GB card, over five calls of eight turns, two from a cold start: the most
# memory in use on the card during a call, the desktop's own (about 600 MiB) included;
# and the most RAM her backend and Ollama held together, as the first call's speech
# models loaded. Windows and other programs are not in the second figure.
CALL_VRAM_MIB = 14_168
CALL_RAM_MIB = 6_327
# nvidia-smi gives up after 10 s and the Ollama listing 5 s after connecting; a check
# still running past this (a network drive gone quiet) is reported as unreadable.
CHECK_TIMEOUT_S = 15.0
# Asked again this soon, the last answer stands: nothing a person does changes it
# faster, and a page asking in a loop costs one run every two seconds.
REUSE_S = 2.0

CheckName = Literal["gpu", "ram", "disk", "llm", "models"]
Verdict = Literal["ok", "warn", "fail", "unknown"]
Code = Literal[
    "ok",
    "small",
    "absent",
    "low",
    "full",
    "unreachable",
    "model_missing",
    "no_tools",
    "missing",
    "unreadable",
]


class Check(BaseModel):
    """How one check came out, and only the values its words need, never a sentence.

    `gpu`: `ok` or `small` with `name`, `total_mib` and `needs_mib`; `absent`. `ram`: `ok`
    or `small` with `total_mib` and `needs_mib`. `disk`: `ok`, `low` or `full` with
    `free_mib` and `needs_mib`. `llm`: `ok`, `model_missing` or `no_tools` with `model`;
    `unreachable`. `models`: `ok` with `count`; `missing` with `models`, codes joined by
    commas. Any check: `unreadable`, when it couldn't be read (verdict `unknown`)."""

    check: CheckName
    verdict: Verdict
    code: Code
    detail: dict[str, str | int] = {}


def gpu_check(card: machine.Card | None) -> Check:
    """By default her speech and memory models run on an NVIDIA card, and calls were
    measured with Ollama's model on the same card."""
    if card is None:
        return Check(check="gpu", verdict="fail", code="absent")
    detail: dict[str, str | int] = {
        "name": card.name,
        "total_mib": card.total_mib,
        "needs_mib": CALL_VRAM_MIB,
    }
    if card.total_mib < CALL_VRAM_MIB:
        return Check(check="gpu", verdict="warn", code="small", detail=detail)
    return Check(check="gpu", verdict="ok", code="ok", detail=detail)


def ram_check(total_mib: int) -> Check:
    detail: dict[str, str | int] = {"total_mib": total_mib, "needs_mib": CALL_RAM_MIB}
    if total_mib < CALL_RAM_MIB:
        return Check(check="ram", verdict="warn", code="small", detail=detail)
    return Check(check="ram", verdict="ok", code="ok", detail=detail)


def disk_check(free_mib: int, needs_mib: int) -> Check:
    """Less than a MiB free, she may not be able to save anything. Short of room for the
    copy an update keeps first, that update waits and she starts without her memory
    (`memory_no_room`)."""
    detail: dict[str, str | int] = {"free_mib": free_mib, "needs_mib": needs_mib}
    if free_mib == 0:
        return Check(check="disk", verdict="fail", code="full", detail=detail)
    if free_mib < needs_mib:
        return Check(check="disk", verdict="warn", code="low", detail=detail)
    return Check(check="disk", verdict="ok", code="ok", detail=detail)


def llm_check(model: str, installed: list[ollama_models.InstalledModel] | None) -> Check:
    """`installed` is None when Ollama didn't answer. Chat and calls offer her tools with
    every request, so a model Ollama says can't use them is no use to her."""
    if installed is None:
        return Check(check="llm", verdict="fail", code="unreachable")
    found = ollama_models.find(installed, model)
    if found is None:
        return Check(check="llm", verdict="fail", code="model_missing", detail={"model": model})
    if found.capabilities is not None and "tools" not in found.capabilities:
        return Check(check="llm", verdict="fail", code="no_tools", detail={"model": model})
    return Check(check="llm", verdict="ok", code="ok", detail={"model": model})


def models_check(required: int, absent: list[model_files.Weights]) -> Check:
    if not absent:
        return Check(check="models", verdict="ok", code="ok", detail={"count": required})
    codes = ",".join(weights.code for weights in absent)
    done_without = all(weights.code in model_files.DONE_WITHOUT for weights in absent)
    return Check(
        check="models",
        verdict="warn" if done_without else "fail",
        code="missing",
        detail={"models": codes},
    )


class Doctor:
    """The check as the app serves it: one run at a time, shared by every request that
    arrives while it runs, and its answer given again for `REUSE_S` after it ends."""

    def __init__(self) -> None:
        self._run: asyncio.Task[list[Check]] | None = None
        self._ended = 0.0

    async def checks(self, settings: Settings) -> list[Check]:
        run = self._run
        if run is None or (run.done() and time.monotonic() - self._ended >= REUSE_S):
            run = self._run = asyncio.create_task(self._examine(settings))
        # Shielded: a request that goes away doesn't cancel the run the others wait on.
        return await asyncio.shield(run)

    async def _examine(self, settings: Settings) -> list[Check]:
        try:
            return await examine(settings)
        finally:
            self._ended = time.monotonic()


async def examine(settings: Settings) -> list[Check]:
    """Every check, in the order the app shows them, run side by side on threads of their
    own: a driver in a bad state, or a port nothing answers on, can take seconds."""
    started = time.perf_counter()
    checks = list(await asyncio.gather(*(_run(name, read, settings) for name, read in _READS)))
    current_blackbox().record(
        "info",
        "doctor.checked",
        duration_ms=round((time.perf_counter() - started) * 1000),
        **{check.check: check.code for check in checks},
    )
    return checks


def _gpu(_settings: Settings) -> Check:
    return gpu_check(machine.nvidia_card())


def _ram(_settings: Settings) -> Check:
    return ram_check(machine.total_memory_mib())


def _disk(settings: Settings) -> Check:
    path = anchored(settings.memory.db_path)
    needs_mib = upgrade_copy.needed_mib(upgrade_copy.room_needed(path))
    return disk_check(machine.free_mib(path.parent), needs_mib)


def _llm(settings: Settings) -> Check:
    try:
        installed = ollama_models.installed_models(settings.llm)
    except OllamaUnreachableError:
        return llm_check(settings.llm.model, None)
    return llm_check(settings.llm.model, installed)


def _models(settings: Settings) -> Check:
    return models_check(len(model_files.required(settings)), model_files.missing(settings))


_READS: tuple[tuple[CheckName, Callable[[Settings], Check]], ...] = (
    ("gpu", _gpu),
    ("ram", _ram),
    ("disk", _disk),
    ("llm", _llm),
    ("models", _models),
)


@functools.cache
def _threads() -> ThreadPoolExecutor:
    """Threads of the check's own: one that hangs holds up the next check, not speech
    recognition or synthesis, which share the loop's default threads."""
    return ThreadPoolExecutor(max_workers=len(_READS), thread_name_prefix="doctor")


async def _run(name: CheckName, read: Callable[[Settings], Check], settings: Settings) -> Check:
    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(_threads(), read, settings)
        return await asyncio.wait_for(future, CHECK_TIMEOUT_S)
    except TimeoutError:
        logger.warning("the %s check didn't answer in %g s", name, CHECK_TIMEOUT_S)
    except machine.ReadingError as error:
        logger.warning("the %s check couldn't be read: %s", name, error)
    except Exception:  # one check that breaks must not take the others with it
        logger.exception("the %s check failed", name)
    return Check(check=name, verdict="unknown", code="unreadable")
