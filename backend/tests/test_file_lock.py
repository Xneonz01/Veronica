"""The lock two starts at once take turns on (`core/file_lock.py`): one holder at a time,
across processes, let go when its holder ends however it ends."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.core.file_lock import Held, held, lock_path

BACKEND = Path(__file__).resolve().parents[1]


def test_the_lock_is_named_after_the_key_it_guards() -> None:
    assert lock_path(Path("data/memory/memory.key")) == Path("data/memory/memory.lock")


def test_a_second_holder_waits_until_the_first_lets_go(tmp_path: Path) -> None:
    """(mutation: take it without waiting)"""
    path = tmp_path / "memory.lock"
    order: list[str] = []

    def second() -> None:
        with held(path):
            order.append("second")

    with held(path):
        waiting = threading.Thread(target=second)
        waiting.start()
        waiting.join(0.3)
        assert waiting.is_alive()
        order.append("first")
    waiting.join(5)

    assert order == ["first", "second"]


def test_a_start_kept_waiting_too_long_is_told_so(tmp_path: Path) -> None:
    path = tmp_path / "memory.lock"
    with held(path), pytest.raises(Held), held(path, wait_s=0.1):
        pass


def test_another_process_holding_it_keeps_this_one_out_until_it_ends(tmp_path: Path) -> None:
    """Ended without letting go, as a crash does, it frees the lock all the same."""
    path = tmp_path / "memory.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, sys\n"
            "from pathlib import Path\n"
            "from app.core.file_lock import held\n"
            f"with held(Path({str(path)!r})):\n"
            "    print('held', flush=True)\n"
            "    sys.stdin.readline()\n"
            "    os._exit(1)\n",
        ],
        cwd=BACKEND,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        with pytest.raises(Held), held(path, wait_s=0.2):
            pass
    finally:
        assert holder.stdin is not None
        holder.stdin.write("go\n")
        holder.stdin.flush()
        holder.wait(10)

    with held(path, wait_s=5):
        pass
