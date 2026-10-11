"""A secret passed to any capability must not reach telemetry, the audit log or the logs.

Every registered capability is run with a secret-shaped canary in every string
argument, with the real black box, perf log and audit store wired in. Then every
byte the telemetry wrote, every audit row and every log message is searched for it.
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any, Literal, get_origin

import httpx
import pytest

from app.adapters.web.fetch import PageFetcher
from app.capabilities import register_capabilities
from app.core.config import Settings
from app.core.config_capabilities import CapabilitySettings
from app.core.event_bus import InProcessEventBus
from app.core.logging import QUIET_AT_DEBUG, QUIET_URLS, quiet_libraries
from app.kernel.capability import CapabilityRequest
from app.kernel.registry import Capability
from app.kernel.runtime import build_kernel_runtime
from app.services.connected.runtime import ConnectedRuntime
from app.services.connected.store import ConnectedSettings, ConnectedStore
from app.telemetry import install, uninstall
from app.telemetry.blackbox import BlackBox
from app.telemetry.hooks import CrashHooks
from app.telemetry.perf import PerfLogger
from app.telemetry.runctx import RunContext
from tests.fakes import build_fake_memory_runtime

# Shaped like a real key, so the redaction rules are exercised rather than sidestepped.
CANARY = "sk-" + secrets.token_hex(20)


def _poisoned(capability: Capability, workdir: Path) -> dict[str, Any]:
    """Arguments for `capability` with the canary in every string field."""
    args: dict[str, Any] = {}
    for name, field in capability.args_model.model_fields.items():
        if field.annotation is bool:
            args[name] = True
        elif name == "path" and capability.name in {"files.list", "files.search"}:
            folder = workdir / CANARY
            folder.mkdir(exist_ok=True)
            args[name] = str(folder)
        elif name == "path":
            args[name] = str(workdir / f"{CANARY}.txt")
        elif field.annotation is str or get_origin(field.annotation) is Literal:
            args[name] = CANARY
        else:  # a new argument type needs a sample here before this test can vouch for it
            raise AssertionError(f"{capability.name}.{name}: no sample for {field.annotation!r}")
    return args


async def _web(bus: InProcessEventBus, root: Path) -> ConnectedRuntime:
    """Web search switched on, against a search engine that answers with the query: the
    canary comes back in the result as well as going out."""

    def echo(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q", "")
        hit = {"title": query, "url": "https://example.org/", "content": query}
        return httpx.Response(200, json={"results": [hit]})

    store = ConnectedStore(bus, root / "connected.json", None)
    await store.update(ConnectedSettings(web_search=True))
    client = httpx.AsyncClient(transport=httpx.MockTransport(echo))
    return ConnectedRuntime(
        store, client, PageFetcher(client, max_bytes=10_000, blocked=store.blocked)
    )


def _sweep(root: Path) -> str:
    return "".join(
        path.read_bytes().decode("utf-8", errors="ignore")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


async def test_a_secret_in_every_argument_reaches_no_telemetry_audit_or_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    # As `setup_logging` does: libraries that log payloads or addresses are held above
    # the app's level, even at DEBUG.
    quiet = {name: logging.getLogger(name).level for name in (*QUIET_AT_DEBUG, *QUIET_URLS)}
    quiet_libraries(logging.DEBUG)
    telemetry = tmp_path / "telemetry"
    workdir = tmp_path / "files"
    workdir.mkdir()
    (workdir / f"{CANARY}.txt").write_text(CANARY, encoding="utf-8")

    run = RunContext.create(app_version="0.0.0", channel="dev")
    perf = PerfLogger(run, telemetry / "perf", max_bytes=1_000_000, files_kept=2)
    box = BlackBox(run, telemetry, queue_size=1000, runs_kept=5, max_run_bytes=1_000_000, clip=2000)
    hooks = CrashHooks(box)
    hooks.install()
    install(perf, box)
    covered: list[str] = []
    try:
        bus = InProcessEventBus()
        memory = await build_fake_memory_runtime(tmp_path / "db", bus)
        store = memory.store
        settings = Settings(
            capabilities=CapabilitySettings(
                file_roots=[str(tmp_path)],
                policy={
                    "files.write": "auto_allow",
                    "apps.open": "auto_allow",
                    "memory.forget": "auto_allow",
                },
            )
        )
        runtime = build_kernel_runtime(settings, bus, store)
        register_capabilities(
            runtime.registry, settings, memory=memory, web=await _web(bus, tmp_path)
        )
        await runtime.init()
        for capability in runtime.registry:
            covered.append(capability.name)
            request = CapabilityRequest(
                name=capability.name, args=_poisoned(capability, workdir), actor="veronica:test"
            )
            await runtime.kernel.execute(request)
        # A refusal path too: an unknown capability named after the secret.
        await runtime.kernel.execute(CapabilityRequest(name=CANARY, args={}, actor="t"))
        rows = await runtime.audit.list(limit=200)
        await runtime.aclose()
        await store.close()
    finally:
        uninstall()
        hooks.uninstall()
        box.close()
        perf.close()
        for name, level in quiet.items():
            logging.getLogger(name).setLevel(level)

    assert covered == [
        "memory.save",
        "memory.recall",
        "memory.correct",
        "memory.forget",
        "files.read",
        "files.list",
        "files.search",
        "files.write",
        "apps.open",
        "web.search",
        "web.read",
    ]
    audit = json.dumps(rows, default=str)
    assert len(rows) == len(covered) + 1
    assert CANARY not in audit, "the canary reached the audit log"
    assert "[redacted]" in audit  # the check above is not passing vacuously

    swept = _sweep(telemetry)
    assert run.run_id in swept and "kernel.invoke" in swept  # telemetry was written
    assert CANARY not in swept, "the canary reached the black box or the perf log"

    for record in caplog.records:
        assert CANARY not in record.getMessage(), f"the canary was logged by {record.name}"
