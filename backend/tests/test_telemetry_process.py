"""Process readings for the black box never raise and never invent a zero."""

from __future__ import annotations

import os
import sys
import threading

from app.telemetry.process import cuda_used_mb, handle_count, rss_mb, thread_count


def test_resident_memory_is_a_real_positive_reading() -> None:
    reading = rss_mb()
    assert isinstance(reading, int) and reading > 0


def test_a_thread_that_is_still_alive_is_counted() -> None:
    before = thread_count()
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    try:
        assert thread_count() == before + 1
    finally:
        release.set()
        worker.join()


def test_an_open_file_shows_up_in_the_handle_count() -> None:
    before = handle_count()
    if before is None:  # a platform with no way to count them
        return
    fd = os.open(__file__, os.O_RDONLY)
    try:
        after = handle_count()
        assert after is not None and after > before
    finally:
        os.close(fd)


def test_vram_is_not_read_when_cuda_was_never_started() -> None:
    # With torch absent from sys.modules the reader must not import it (and so must not
    # create a CUDA context just to take a reading).
    saved = sys.modules.pop("torch", None)
    try:
        assert cuda_used_mb() is None
        assert "torch" not in sys.modules
    finally:
        if saved is not None:
            sys.modules["torch"] = saved
