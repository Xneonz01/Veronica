"""The check of this computer: each verdict against the measured figures and the hard
limits, run side by side off the event loop without one broken or stuck check taking the
others, shared by the requests that ask while it runs, and served to the app and the
command line."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import doctor
from app.adapters import model_files
from app.adapters.llm import ollama_models
from app.adapters.llm.ollama_errors import OllamaUnreachableError
from app.adapters.llm.ollama_models import InstalledModel
from app.core import machine
from app.core.config import Settings
from app.main import create_app
from scripts import diagnostics

CARD = machine.Card("NVIDIA Example Card", 16_376)
TOOLS = frozenset({"completion", "tools"})


def weights(code: str) -> model_files.Weights:
    return model_files.Weights(code, "a role", "a model", lambda: None)


# Every outcome the checks can give, but `unreadable`, which any check gives the same way.
OUTCOMES = [
    doctor.gpu_check(CARD),
    doctor.gpu_check(machine.Card("Smaller", 8_192)),
    doctor.gpu_check(None),
    doctor.ram_check(32_000),
    doctor.ram_check(4_096),
    doctor.disk_check(10, 4),
    doctor.disk_check(3, 4),
    doctor.disk_check(0, 4),
    doctor.llm_check("qwen3.5:9b", None),
    doctor.llm_check("qwen3.5:9b", []),
    doctor.llm_check("qwen3.5:9b", [InstalledModel("qwen3.5:9b", frozenset())]),
    doctor.llm_check("qwen3.5:9b", [InstalledModel("qwen3.5:9b", TOOLS)]),
    doctor.models_check(5, []),
    doctor.models_check(5, [weights("tts")]),
]


def test_a_card_as_big_as_calls_needed_is_enough_and_a_smaller_one_is_flagged() -> None:
    enough = doctor.gpu_check(machine.Card("Exact", doctor.CALL_VRAM_MIB))
    small = doctor.gpu_check(machine.Card("Smaller", doctor.CALL_VRAM_MIB - 1))

    assert (enough.verdict, enough.code) == ("ok", "ok")
    assert (small.verdict, small.code) == ("warn", "small")
    assert small.detail == {
        "name": "Smaller",
        "total_mib": doctor.CALL_VRAM_MIB - 1,
        "needs_mib": doctor.CALL_VRAM_MIB,
    }


def test_no_card_fails() -> None:
    assert doctor.gpu_check(None).model_dump() == {
        "check": "gpu",
        "verdict": "fail",
        "code": "absent",
        "detail": {},
    }


def test_memory_is_held_to_what_calls_used() -> None:
    assert doctor.ram_check(doctor.CALL_RAM_MIB).verdict == "ok"
    small = doctor.ram_check(doctor.CALL_RAM_MIB - 1)
    assert (small.verdict, small.code) == ("warn", "small")
    assert small.detail == {"total_mib": doctor.CALL_RAM_MIB - 1, "needs_mib": doctor.CALL_RAM_MIB}


def test_a_disk_short_of_room_or_full_is_flagged() -> None:
    assert doctor.disk_check(4, 4).verdict == "ok"
    low = doctor.disk_check(3, 4)
    assert (low.verdict, low.code, low.detail) == ("warn", "low", {"free_mib": 3, "needs_mib": 4})
    # With no memory file yet nothing is needed for a copy, but a full disk still fails.
    full = doctor.disk_check(0, 0)
    assert (full.verdict, full.code) == ("fail", "full")


@pytest.mark.parametrize(
    ("installed", "code"),
    [
        (None, "unreachable"),
        ([InstalledModel("qwen3:8b", TOOLS)], "model_missing"),
        ([InstalledModel("qwen3.5:9b", frozenset({"completion"}))], "no_tools"),
        ([InstalledModel("qwen3.5:9b", None)], "ok"),  # an Ollama that doesn't say
        ([InstalledModel("qwen3.5:9b", TOOLS)], "ok"),
    ],
)
def test_the_language_model_must_be_installed_and_able_to_use_tools(
    installed: list[InstalledModel] | None, code: str
) -> None:
    check = doctor.llm_check("qwen3.5:9b", installed)
    assert check.code == code
    assert check.verdict == ("ok" if code == "ok" else "fail")
    assert check.detail == ({} if code == "unreachable" else {"model": "qwen3.5:9b"})


def test_missing_model_files_fail_unless_she_does_without_them() -> None:
    assert doctor.models_check(5, []).detail == {"count": 5}
    optional = doctor.models_check(5, [weights("turn"), weights("rerank")])
    assert (optional.verdict, optional.detail) == ("warn", {"models": "turn,rerank"})
    needed = doctor.models_check(5, [weights("turn"), weights("embedding")])
    assert (needed.verdict, needed.code, needed.detail) == (
        "fail",
        "missing",
        {"models": "turn,embedding"},
    )


@pytest.fixture
def readings(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A computer that has everything, whatever this one has."""
    monkeypatch.setattr(machine, "nvidia_card", lambda: CARD)
    monkeypatch.setattr(machine, "total_memory_mib", lambda: 32_000)
    monkeypatch.setattr(machine, "free_mib", lambda _path: 500_000)
    model = InstalledModel(Settings().llm.model, TOOLS)
    monkeypatch.setattr(ollama_models, "installed_models", lambda _settings: [model])
    return monkeypatch


async def test_every_check_runs_in_the_order_the_app_shows_them(
    readings: pytest.MonkeyPatch,
) -> None:
    checks = await doctor.examine(Settings())
    assert [(check.check, check.code) for check in checks] == [
        ("gpu", "ok"),
        ("ram", "ok"),
        ("disk", "ok"),
        ("llm", "ok"),
        ("models", "ok"),
    ]


async def test_the_checks_run_side_by_side_and_off_the_event_loop(
    readings: pytest.MonkeyPatch,
) -> None:
    # Each waits for the other: one after the other, the first would wait out the barrier.
    together = threading.Barrier(2, timeout=5)

    def card() -> machine.Card:
        together.wait()
        with pytest.raises(RuntimeError):  # no event loop in this thread
            asyncio.get_running_loop()
        return CARD

    def memory() -> int:
        together.wait()
        return 32_000

    readings.setattr(machine, "nvidia_card", card)
    readings.setattr(machine, "total_memory_mib", memory)

    checks = {check.check: check.code for check in await doctor.examine(Settings())}
    assert checks["gpu"] == checks["ram"] == "ok"


async def test_a_check_that_breaks_is_unknown_and_the_others_still_run(
    readings: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unreadable() -> None:
        raise machine.ReadingError("nvidia-smi gave the card's memory as '[N/A]'")

    def broken(_path: Path) -> int:
        raise PermissionError("closed")

    def refused(_settings: Any) -> None:
        raise OllamaUnreachableError("refused")

    readings.setattr(machine, "nvidia_card", unreadable)
    readings.setattr(machine, "free_mib", broken)
    readings.setattr(ollama_models, "installed_models", refused)

    checks = {check.check: check for check in await doctor.examine(Settings())}

    assert (checks["gpu"].verdict, checks["gpu"].code) == ("unknown", "unreadable")
    assert (checks["disk"].verdict, checks["disk"].code) == ("unknown", "unreadable")
    assert checks["llm"].code == "unreachable"
    assert checks["ram"].code == checks["models"].code == "ok"
    # An answer it couldn't read is a line in the log; a fault of its own, a traceback.
    logged = {record.getMessage(): record for record in caplog.records}
    card = logged["the gpu check couldn't be read: nvidia-smi gave the card's memory as '[N/A]'"]
    assert (card.levelname, card.exc_info) == ("WARNING", None)
    assert logged["the disk check failed"].exc_info is not None


async def test_a_check_that_hangs_is_unknown_once_its_time_is_up(
    readings: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    released = threading.Event()

    def stuck(_path: Path) -> int:
        released.wait(5)  # a network drive gone quiet
        return 500_000

    readings.setattr(doctor, "CHECK_TIMEOUT_S", 0.2)
    readings.setattr(machine, "free_mib", stuck)
    try:
        checks = {check.check: check.code for check in await doctor.examine(Settings())}
    finally:
        released.set()

    assert checks == {"gpu": "ok", "ram": "ok", "disk": "unreadable", "llm": "ok", "models": "ok"}
    assert "the disk check didn't answer in 0.2 s" in caplog.text


async def test_the_disk_is_asked_where_her_memory_file_is_for_its_copys_room(
    tmp_path: Path, readings: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "memory" / "memory.db"
    path.parent.mkdir()
    path.write_bytes(b"x" * 3_000_000)
    asked: list[Path] = []

    def free(folder: Path) -> int:
        asked.append(folder)
        return 3

    readings.setattr(machine, "free_mib", free)
    settings = Settings()
    settings.memory.db_path = str(path)

    disk = next(check for check in await doctor.examine(settings) if check.check == "disk")

    assert asked == [path.parent]
    assert disk.detail == {"free_mib": 3, "needs_mib": 4}  # 3.3 MB, rounded up to MiB
    assert disk.code == "low"


async def test_what_it_found_is_recorded_as_codes_only(
    readings: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, str, dict[str, Any]]] = []

    class Box:
        def record(self, level: str, event: str, **fields: Any) -> None:
            recorded.append((level, event, fields))

    readings.setattr(doctor, "current_blackbox", Box)
    readings.setattr(machine, "nvidia_card", lambda: None)
    await doctor.examine(Settings())

    ((level, event, fields),) = recorded
    assert (level, event) == ("info", "doctor.checked")
    assert isinstance(fields.pop("duration_ms"), int)
    # The outcome's code, not its verdict ("fail") and not the card's name.
    assert fields == {"gpu": "absent", "ram": "ok", "disk": "ok", "llm": "ok", "models": "ok"}


async def test_requests_while_it_runs_share_one_run_and_a_fresh_answer_is_reused(
    readings: pytest.MonkeyPatch,
) -> None:
    runs = 0
    examine = doctor.examine

    async def counted(settings: Settings) -> list[doctor.Check]:
        nonlocal runs
        runs += 1
        await asyncio.sleep(0.05)  # long enough for the second request to arrive
        return await examine(settings)

    readings.setattr(doctor, "examine", counted)
    runner = doctor.Doctor()

    first, second = await asyncio.gather(runner.checks(Settings()), runner.checks(Settings()))
    assert runs == 1 and first == second
    await runner.checks(Settings())  # asked again straight away: the same answer
    assert runs == 1
    readings.setattr(doctor, "REUSE_S", 0.0)
    await runner.checks(Settings())
    assert runs == 2


def test_the_app_is_served_every_check(readings: pytest.MonkeyPatch) -> None:
    with TestClient(create_app(), base_url="http://localhost") as client:
        body = client.get("/diagnostics/doctor").json()
    assert [check["check"] for check in body["checks"]] == ["gpu", "ram", "disk", "llm", "models"]
    assert body["checks"][0] == {
        "check": "gpu",
        "verdict": "ok",
        "code": "ok",
        "detail": {
            "name": CARD.name,
            "total_mib": CARD.total_mib,
            "needs_mib": doctor.CALL_VRAM_MIB,
        },
    }


@pytest.mark.parametrize("check", OUTCOMES, ids=lambda check: f"{check.check}-{check.code}")
def test_the_command_line_has_words_of_its_own_for_every_outcome(check: doctor.Check) -> None:
    assert (check.check, check.code) in diagnostics._WORDS
    words = diagnostics.describe(check.model_dump(), "http://127.0.0.1:11434")
    assert words and "{" not in words


def test_the_command_line_prints_the_check_after_the_report(
    readings: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = diagnostics.collect(with_events=False, with_doctor=True)
    assert [check["check"] for check in report["computer"]] == [
        "gpu",
        "ram",
        "disk",
        "llm",
        "models",
    ]
    diagnostics._print(report)
    printed = capsys.readouterr().out
    assert "This computer, against what Veronica was measured using:" in printed
    assert "NVIDIA Example Card, 16,376 MiB; calls used up to 14,168 MiB" in printed
