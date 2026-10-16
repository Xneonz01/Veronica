"""FastAPI application factory and async lifespan."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import left_behind, startup
from app.adapters.model_files import MEMORY_MODEL
from app.api import http_guards
from app.api.host_guard import HostGuard
from app.api.routes_audit import router as audit_router
from app.api.routes_capabilities import router as capabilities_router
from app.api.routes_chat import router as chat_router
from app.api.routes_connected import router as connected_router
from app.api.routes_events import router as events_router
from app.api.routes_health import router as health_router
from app.api.routes_mcp import router as mcp_router
from app.api.routes_memory import router as memory_router
from app.api.routes_persona import router as persona_router
from app.api.routes_projects import router as projects_router
from app.api.routes_rag import router as rag_router
from app.api.routes_recovery import router as recovery_router
from app.api.routes_system import router as system_router
from app.api.routes_voices import router as voices_router
from app.api.routes_ws_chat import router as ws_chat_router
from app.api.routes_ws_voice import router as ws_voice_router
from app.api.uploads import sweep_uploads
from app.core import data_acl
from app.core.changelog import ChangelogError, latest
from app.core.config import Settings, get_settings
from app.core.event_bus import InProcessEventBus
from app.core.logging import setup_logging
from app.core.origins import ALLOWED_ORIGINS
from app.core.version import APP_VERSION, APP_VERSION_LABEL
from app.doctor import Doctor
from app.services.persona import PersonaService
from app.services.voice.runtime import VoiceRuntime, build_voice_runtime
from app.stores import Stores, open_stores
from app.telemetry import current_perf
from app.telemetry.process import cuda_used_mb
from app.telemetry.runtime import start_telemetry, stop_telemetry

logger = logging.getLogger(__name__)

_BACKGROUND_DRAIN_S = 10.0  # session-end learning is a one-shot extraction, seconds


def _version_label() -> str:
    """The newest changelog label, read once; the built-in one if the file is unreadable."""
    try:
        return latest().label
    except ChangelogError as exc:
        logger.warning("could not read CHANGELOG.yaml (%s); using the built-in label", exc)
        return APP_VERSION_LABEL


def websocket_stack_available() -> bool:
    """True if the ASGI WebSocket transport is importable.

    The live call needs uvicorn[standard] -> `websockets` (or `wsproto`). Without
    it the `/ws/voice` handshake fails before the app sees it, which is hard to
    diagnose. Checking at startup makes a mis-installed server fail loudly.
    """
    return (
        importlib.util.find_spec("websockets") is not None
        or importlib.util.find_spec("wsproto") is not None
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the runtimes and stores on startup; drain and close them on shutdown.

    The memory model's load starts here, in the background (`startup.warm_memory_model`),
    and the chat model's through Ollama (`startup.warm_chat_model`); the GPU manager
    loads the others on first use.
    """
    started = time.perf_counter()
    settings: Settings = app.state.settings
    setup_logging(settings.logging.level)
    if not websocket_stack_available():
        logger.critical(
            "WebSocket stack missing: install 'uvicorn[standard]' (provides websockets). "
            "The /ws/voice live call cannot accept connections without it."
        )
        raise RuntimeError("ASGI WebSocket support unavailable — install uvicorn[standard]")
    # Private to this account before anything writes into it, the black box's own files
    # included; what was done is recorded once the black box is up.
    data_dir = settings.data_path()
    kept = await data_acl.keep_private(data_dir)
    await asyncio.to_thread(sweep_uploads, data_dir)  # copies a crash left behind
    # First, so everything after it, including a failed start-up, is recorded.
    telemetry = start_telemetry(settings.telemetry, asyncio.get_running_loop())
    app.state.telemetry = telemetry
    data_acl.record(kept)
    # Session-end summary and learning tasks are tracked here so shutdown can wait for
    # them instead of closing the DB mid-write and losing the call's memory.
    background: set[asyncio.Task[None]] = set()
    app.state.background_tasks = background
    # Whatever gets built is closed again in `finally`, even if start-up fails halfway.
    voice_runtime: VoiceRuntime | None = None
    stores: Stores | None = None
    learning_task: asyncio.Task[None] | None = None
    warming: asyncio.Task[None] | None = None
    serving = False
    startup_error: BaseException | None = None
    app.state.problems = []
    try:
        app.state.version_label = _version_label()
        left_behind.warn(settings)  # said before anything opens them
        logger.info(
            "Veronica %s starting | run=%s profile=%s env=%s gpu_budget=%sGB",
            APP_VERSION,
            telemetry.run.run_id,
            settings.profile,
            settings.app.env,
            settings.gpu.vram_budget_gb,
        )
        app.state.event_bus = InProcessEventBus()
        voice_runtime = await build_voice_runtime(settings, app.state.event_bus)
        app.state.voice_runtime = voice_runtime
        startup.voices_problem(app, voice_runtime)
        # One personality for voice and text, read per turn so an edit applies live.
        persona = PersonaService(data_dir / "persona.json", app.state.event_bus)
        persona.load()
        app.state.persona = persona
        # Memory registers its model on the voice runtime's GPU manager: one manager owns
        # every model. A memory file she can't use (a newer build's, damaged, held, or
        # no room to upgrade it) leaves her without what lives in it, and /health says
        # why, instead of stopping the backend (`app/stores.py`).
        try:
            stores = await open_stores(
                settings, app.state.event_bus, voice_runtime.manager, data_dir
            )
        except Exception as error:
            if not startup.without_memory(app, error):
                raise
        else:
            learning_task = startup.serve(app, stores, voice_runtime, settings, background)
            startup.hold_persona_to_window(persona, stores.kernel, settings.llm)
            persona.web_with(stores.web.store.web_on)
            persona.servers_with(stores.mcp.reaching_internet)
        absent = await startup.check_models(app, settings)
        # Not for a memory she started without, or a model that isn't there to load.
        if stores is not None and settings.memory.warm_on_start and MEMORY_MODEL not in absent:
            startup.track(background, startup.warm_memory_model(stores.memory.embedder))
        if settings.llm.warm_on_start:  # beside the memory model, not after it
            # Not among the work shutdown waits for: a load isn't worth finishing for a
            # quit, which waited up to 10 s for it.
            warming = asyncio.create_task(startup.warm_chat_model(voice_runtime.llm, settings))
        current_perf().log("app.startup", (time.perf_counter() - started) * 1000.0)
        serving = True
        yield
    except BaseException as exc:
        # Uvicorn logs a failed start only after this generator has closed the black
        # box, so the error is handed to it here.
        if not serving:
            startup_error = exc
        raise
    finally:
        if warming is not None:
            warming.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await warming
        if learning_task is not None:
            # Not drained: an extraction cut off here never reached `done`, so its
            # conversation is still due at the next start.
            learning_task.cancel()
            # `run` only ends by being cancelled; anything else must not stop the rest
            # of shutdown either.
            with suppress(asyncio.CancelledError, Exception):
                await learning_task
        if background:
            logger.info("draining %d background task(s) before shutdown", len(background))
            await asyncio.wait(background, timeout=_BACKGROUND_DRAIN_S)
            for task in background:
                task.cancel()
        # Deny anything still awaiting a human answer, then let its audit row land.
        if stores is not None:
            with suppress(Exception):  # first: a server's call may be awaiting the kernel
                await stores.mcp.aclose()
            with suppress(Exception):
                await stores.kernel.aclose()
            with suppress(Exception):
                await stores.web.aclose()
        if voice_runtime is not None:
            voice_runtime.calls.close()  # no release of the speech models mid-shutdown
            with suppress(Exception):
                # keep_alive would hold the model in VRAM for half an hour after quit.
                await voice_runtime.llm.release_model()
            with suppress(Exception):
                await voice_runtime.llm.aclose()
            try:
                # Her own models too, so the reading `app.stop` takes shows what is left.
                await voice_runtime.manager.unload_all()
            except Exception:
                logger.warning("her models couldn't all be released at shutdown", exc_info=True)
        try:
            if stores is not None:
                await stores.memory.store.close()
        finally:
            # Last, so `app.stop` is the final event and `clean_exit` is only written
            # once everything above has finished.
            stop_telemetry(telemetry, vram_used_mb=cuda_used_mb(), startup_error=startup_error)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application, with `settings` or the ones the
    configuration files and the environment give (`get_settings`)."""
    settings = settings or get_settings()
    # Offline, forced rather than defaulted: a shell that set it to 0 must not reopen
    # the network. The hub reads it when first imported, which is in a loader, after
    # this (`tests/test_model_files.py` holds that). Every loader also passes
    # `local_files_only`; this reaches the calls no argument of ours can.
    os.environ["HF_HUB_OFFLINE"] = "1"
    app = FastAPI(title=settings.app.name, lifespan=lifespan)
    app.state.settings = settings
    # The check of this computer, shared by the requests that ask while it runs.
    app.state.doctor = Doctor()

    http_guards.install(app)

    # The frontend (Vite dev server or the Tauri webview) calls the REST API
    # cross-origin; only its exact origins are allowed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added last so it runs first, before CORS answers anything: a request addressed
    # to a rebound host name is refused outright, on HTTP and WebSockets alike.
    app.add_middleware(HostGuard)
    app.include_router(health_router)
    app.include_router(system_router)
    app.include_router(ws_voice_router)
    app.include_router(voices_router)
    app.include_router(events_router)
    app.include_router(ws_chat_router)
    app.include_router(memory_router)
    app.include_router(rag_router)
    app.include_router(capabilities_router)
    app.include_router(connected_router)
    app.include_router(mcp_router)
    app.include_router(audit_router)
    app.include_router(recovery_router)
    app.include_router(persona_router)
    app.include_router(chat_router)
    app.include_router(projects_router)
    return app


app = create_app()
