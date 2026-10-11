"""Crash hooks feed the black box and put the previous hooks back afterwards."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from app.telemetry.blackbox import BlackBox
from app.telemetry.hooks import CrashHooks
from app.telemetry.runctx import RunContext


def _box(directory: Path) -> BlackBox:
    run = RunContext.create(app_version="9.9.9", channel="dev")
    return BlackBox(run, directory, queue_size=100, runs_kept=5, max_run_bytes=1_000_000, clip=2000)


def _events(box: BlackBox) -> list[dict[str, Any]]:
    path = box.run_dir / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_hooks_are_installed_and_then_restored(tmp_path: Path) -> None:
    box = _box(tmp_path)
    before_sys, before_thread = sys.excepthook, threading.excepthook
    hooks = CrashHooks(box)
    hooks.install()
    assert sys.excepthook != before_sys and threading.excepthook != before_thread
    hooks.uninstall()
    assert sys.excepthook == before_sys and threading.excepthook == before_thread
    box.close()


def test_an_uncaught_exception_is_recorded_as_fatal(tmp_path: Path) -> None:
    box = _box(tmp_path)
    hooks = CrashHooks(box)
    seen: list[type[BaseException]] = []
    hooks._prev_excepthook = lambda exc_type, exc, tb: seen.append(exc_type)
    try:
        raise RuntimeError("it broke")
    except RuntimeError as exc:
        hooks._on_exception(RuntimeError, exc, exc.__traceback__)
    box.close()
    crash = [e for e in _events(box) if e["event"] == "crash.unhandled_exception"]
    assert crash and crash[0]["level"] == "fatal"
    fields = crash[0]["fields"]
    assert fields["type"] == "RuntimeError" and "it broke" in fields["traceback"]
    assert seen == [RuntimeError]  # the previous hook still ran


def test_a_thread_that_dies_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A quiet "previous" hook keeps the traceback out of the test output; monkeypatch
    # puts the real one back afterwards.
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    box = _box(tmp_path)
    hooks = CrashHooks(box)
    hooks.install()
    try:
        worker = threading.Thread(target=lambda: 1 / 0, name="worker")
        worker.start()
        worker.join()
    finally:
        hooks.uninstall()
    box.close()
    [event] = [e for e in _events(box) if e["event"] == "thread.unhandled_exception"]
    assert event["fields"]["type"] == "ZeroDivisionError" and event["fields"]["thread"] == "worker"


async def test_an_unhandled_task_error_is_recorded(tmp_path: Path) -> None:
    box = _box(tmp_path)
    loop = asyncio.get_running_loop()
    hooks = CrashHooks(box)
    hooks.install(loop)
    previous = hooks._prev_loop_handler
    hooks._prev_loop_handler = lambda _loop, _context: None  # keep the test output quiet
    try:
        loop.call_exception_handler({"message": "Task exception", "exception": ValueError("x")})
    finally:
        hooks._prev_loop_handler = previous
        hooks.uninstall()
    box.close()
    [event] = [e for e in _events(box) if e["event"] == "asyncio.unhandled_exception"]
    assert event["fields"]["type"] == "ValueError"


def test_warnings_from_the_logs_are_copied_but_debug_lines_are_not(tmp_path: Path) -> None:
    box = _box(tmp_path)
    hooks = CrashHooks(box)
    hooks.install()
    try:
        log = logging.getLogger("app.somewhere")
        log.debug("quiet")
        log.warning("disk nearly full")
        logging.getLogger("app.telemetry.perf").warning("our own line")  # never fed back
    finally:
        hooks.uninstall()
    box.close()
    names = [(e["event"], e["fields"].get("message")) for e in _events(box)]
    assert ("log.warning", "disk nearly full") in names
    assert all(message != "quiet" and message != "our own line" for _, message in names)
