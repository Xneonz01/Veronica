"""What a start does once the runtimes are built: the stores put to work, or the reason
she starts without them; her personality held to chat's window; the models on disk
checked; and the memory model loaded ahead of the first message. A memory she starts
without and a model not on disk are problems `/health` reports (`core/problems.py`); a
warm-up that fails is only logged, and left for the first use to report."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from fastapi import FastAPI

from app.adapters import model_files
from app.adapters.base import EmbeddingProvider
from app.adapters.llm.ollama_client import OllamaLLMClient
from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaUnreachableError,
)
from app.core.config import Settings
from app.core.config_llm import LlmSettings
from app.core.logging import log_failure
from app.core.problems import Problem, announce
from app.kernel.audit_integrity import AuditIntegrity, check_chain
from app.kernel.runtime import KernelRuntime
from app.services.chat import window
from app.services.chat.learning_queue import LearningQueue
from app.services.chat.learning_worker import LearningWorker, transcript_budget
from app.services.persona import PersonaService
from app.services.tools import ToolBroker
from app.services.voice.clips import UNUSABLE
from app.services.voice.realtime import card_room
from app.services.voice.runtime import VoiceRuntime
from app.stores import Stores, memory_problem
from app.telemetry import current_blackbox

logger = logging.getLogger(__name__)


def voices_problem(app: FastAPI, runtime: VoiceRuntime) -> None:
    """A voices file she couldn't read at start (`VoiceRegistry`) is a problem `/health`
    reports: set aside (`voices_damaged`), there is no saved voice until one is made
    again; stuck where it is, or in a folder she can't use (`voices_unusable`), none can
    be saved until that is put right. Clips whose key can't be used here
    (`voices_locked`, by reason, `clips.py`) are spoken in the voice model's own voice,
    and none can be made, played or chosen, until it can."""
    if runtime.voices_stuck or runtime.voices_locked == UNUSABLE:
        app.state.problems.append(Problem(code="voices_unusable"))
        return
    if runtime.voices_locked is not None:
        detail: dict[str, str | int] = {"reason": runtime.voices_locked}
        if runtime.voices_recoverable:
            detail["recoverable"] = 1
        app.state.problems.append(Problem(code="voices_locked", detail=detail))
    if runtime.voices_damaged:
        app.state.problems.append(Problem(code="voices_damaged"))


def hold_persona_to_window(
    persona: PersonaService, kernel: KernelRuntime, llm: LlmSettings
) -> None:
    """A personality whose chat prompt leaves no room for a message is refused when it is
    saved: kept, it would send every message back as having none (`chat/window.py`)."""
    chat = ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")
    room = window.prompt_room(llm.num_ctx, llm.reply_tokens, window.tool_tokens(chat.schemas()))
    persona.check_window_with(
        lambda prompt: window.message_room(prompt, room) >= window.MIN_MESSAGE_TOKENS
    )


async def check_audit(app: FastAPI, integrity: AuditIntegrity) -> None:
    """The start's walk of the audit chain (`kernel/audit_integrity.py`). What it finds
    goes to the black box, and a break is a problem the app shows, with a way to mark
    it as reviewed: announced, since the app may have read the problems already."""
    report = await check_chain(integrity)
    if report is None or report.ok:
        return
    row = report.broken_at or 0
    app.state.problems.append(Problem(code="audit_broken", detail={"row": row}))
    await announce(app.state.event_bus)


def track(background: set[asyncio.Task[None]], work: Coroutine[Any, Any, None]) -> None:
    """Run `work` as a task shutdown waits for (`main.lifespan`)."""
    task = asyncio.create_task(work)
    background.add(task)
    task.add_done_callback(background.discard)


def serve(
    app: FastAPI,
    stores: Stores,
    voice: VoiceRuntime,
    settings: Settings,
    background: set[asyncio.Task[None]],
) -> asyncio.Task[None]:
    """Put the stores to work: on `app.state` for the routes, the audit chain walked, and
    chat learned from; the learning worker's task."""
    app.state.problems.extend(stores.problems)  # found opening it, not stopping it
    app.state.memory_runtime = stores.memory
    app.state.kernel_runtime = stores.kernel
    app.state.chat_store = stores.chat
    app.state.attachment_store = stores.attachments
    app.state.project_store = stores.projects
    app.state.connected = stores.web
    # Walked in the background: a long log takes a moment, and a start must not wait on
    # it. After the upgrade, which may give an older chain its head first.
    track(background, check_audit(app, stores.kernel.integrity))
    # Every stored chat message marks its conversation, and every finished call is
    # kept; this learns from them once the model is free, for as long as it runs.
    learning = LearningWorker(
        LearningQueue(stores.memory.store),
        stores.chat,
        stores.memory.learning,
        voice.llm.background(),
        voice.llm.activity,
        calls_live=voice.calls.count,
        model=settings.llm.model,
        quiet_s=settings.memory.learning_quiet_s,
        max_chars=transcript_budget(settings.llm.num_ctx),
        calls=stores.memory.calls,
    )
    return asyncio.create_task(learning.run())


def without_memory(app: FastAPI, error: Exception) -> bool:
    """Whether the start can go on without the stores `error` kept from opening: said to
    the person as a problem, with its cause kept for whoever reads the log."""
    problem = memory_problem(error)
    if problem is None:
        return False
    # With its cause: a failed copy says why only there.
    logger.error("starting without her memory (%s): %s", problem.code, error, exc_info=error)
    cause = error.__cause__
    current_blackbox().record(
        "error",
        "app.degraded",
        code=problem.code,
        error=type(error).__name__,
        cause=type(cause).__name__ if cause is not None else None,
        sqlite=getattr(cause if cause is not None else error, "sqlite_errorname", None),
    )
    app.state.problems.append(problem)
    return True


async def check_models(app: FastAPI, settings: Settings) -> set[str]:
    """The codes of the models not on disk, said in the log and as a problem. Checked,
    not fetched: a missing model fails where it is first used, and this says which one
    before that happens. Only a report, so nothing it raises may stop a start that chat
    alone could use."""
    try:
        absent = await asyncio.to_thread(model_files.missing, settings)
    except Exception:
        logger.exception("could not check which models are on disk")
        return set()
    if absent:
        logger.error("%s", model_files.sentence(absent))
        codes = ",".join(weights.code for weights in absent)
        app.state.problems.append(Problem(code="models_missing", detail={"models": codes}))
    return {weights.code for weights in absent}


async def warm_memory_model(embedder: EmbeddingProvider) -> None:
    """Load the memory model now and run it once: the load from disk (4.0 to 4.5 s,
    measured) and the first pass (about 0.3 s) were both the first message's to wait
    for. A failure here is left for that first use to report, as before."""
    try:
        await embedder.embed(["warm up"])
    except Exception:
        logger.exception("could not load the memory model at start")


async def warm_chat_model(llm: OllamaLLMClient, settings: Settings) -> None:
    """Load the chat model now, so the first message doesn't wait for it (`warm_on_start`
    in `core/config_llm.py`), if the card has room: another program may be using it, and
    the first message would only meet the same card later. A failure is left for that
    first message to report, in words the app has: a log line here says only what kind."""
    if not await card_room.fits_her_model(settings):
        logger.info("not loading the chat model at start: the card hasn't room for it now")
        return
    try:
        await llm.warm(settings.llm.model)
    except (OllamaUnreachableError, OllamaModelMissingError, OllamaModelFailedError) as exc:
        logger.warning("could not load the chat model at start (%s)", type(exc).__name__)
    except Exception as exc:  # anything else sat unseen in the task until a quit dropped it
        log_failure(logger, "could not load the chat model at start", exc)
