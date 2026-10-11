"""What this computer has, read without side effects: the NVIDIA card from nvidia-smi,
the memory, and free space asked of a folder that exists."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import machine


class Smi:
    """nvidia-smi, found and answering as told; what it was run with is kept."""

    def __init__(self, stdout: str = "", returncode: int = 0, error: Exception | None = None):
        self.stdout, self.returncode, self.error = stdout, returncode, error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"args": args, **kwargs})
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


@pytest.fixture
def smi(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(fake: Smi) -> Smi:
        monkeypatch.setattr(machine, "_smi", lambda: "nvidia-smi")
        monkeypatch.setattr(machine.subprocess, "run", fake)
        return fake

    return install


def test_the_card_is_named_with_its_memory_and_no_window_opens(smi: Any) -> None:
    fake = smi(Smi("NVIDIA Example Card, 16376\n"))
    assert machine.nvidia_card() == machine.Card("NVIDIA Example Card", 16376)
    expected = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    assert fake.calls[0]["creationflags"] == expected


def test_the_card_with_the_most_memory_counts(smi: Any) -> None:
    smi(Smi("Card A, 8192\nCard B, 24576\nCard C, 12288\n"))
    assert machine.nvidia_card() == machine.Card("Card B", 24576)


@pytest.mark.parametrize("returncode", [6, 9, 12])
def test_no_device_or_no_driver_is_no_card(smi: Any, returncode: int) -> None:
    # 6 measured ("No devices were found"); 9 and 12 per nvidia-smi's documentation.
    smi(Smi("No devices were found\n", returncode))
    assert machine.nvidia_card() is None


@pytest.mark.parametrize("returncode", [2, 4, 14, 15, 255])
def test_a_card_that_answers_badly_is_unknown_not_absent(smi: Any, returncode: int) -> None:
    smi(Smi("", returncode))
    with pytest.raises(machine.ReadingError, match=str(returncode)):
        machine.nvidia_card()


def test_a_tool_that_hangs_is_unknown(smi: Any) -> None:
    smi(Smi(error=subprocess.TimeoutExpired("nvidia-smi", 10.0)))
    with pytest.raises(machine.ReadingError, match="didn't answer"):
        machine.nvidia_card()


def test_a_tool_that_vanished_is_no_card_and_one_refused_is_unknown(smi: Any) -> None:
    smi(Smi(error=FileNotFoundError("gone")))
    assert machine.nvidia_card() is None
    smi(Smi(error=PermissionError("denied")))
    with pytest.raises(machine.ReadingError, match="PermissionError"):
        machine.nvidia_card()


def test_no_rows_is_no_card(smi: Any) -> None:
    smi(Smi(""))
    assert machine.nvidia_card() is None


def test_without_the_tool_there_is_no_card(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(machine, "_smi", lambda: None)
    assert machine.nvidia_card() is None


@pytest.mark.parametrize("row", ["NVIDIA Example Card, [N/A]", ", 16376", "16376"])
def test_an_answer_it_cant_read_is_unknown_not_absent(smi: Any, row: str) -> None:
    smi(Smi(row))
    with pytest.raises(machine.ReadingError):
        machine.nvidia_card()


def test_the_cards_memory_in_use_is_read_for_every_program(smi: Any) -> None:
    fake = smi(Smi("11838, 16376\n"))  # as measured, another program holding 1.5 GiB
    assert machine.card_use() == machine.CardUse(used_mib=11838, total_mib=16376)
    assert "--query-gpu=memory.used,memory.total" in fake.calls[0]["args"]


def test_with_more_than_one_card_the_one_in_use_isnt_known(smi: Any) -> None:
    smi(Smi("1792, 16376\n900, 8192\n"))
    assert machine.card_use() is None


@pytest.mark.parametrize("stdout", ["", "No devices were found\n"])
def test_no_card_is_no_reading(smi: Any, stdout: str) -> None:
    smi(Smi(stdout, 0 if not stdout else 6))
    assert machine.card_use() is None


@pytest.mark.parametrize("row", ["[N/A], 16376", "1792, [N/A]", "1792", ", 16376"])
def test_memory_in_use_it_cant_read_is_unknown(smi: Any, row: str) -> None:
    smi(Smi(f"{row}\n"))
    with pytest.raises(machine.ReadingError, match="nvidia-smi gave"):
        machine.card_use()


def test_a_card_in_trouble_is_unknown_for_its_memory_in_use_too(smi: Any) -> None:
    smi(Smi(error=subprocess.TimeoutExpired("nvidia-smi", 10.0)))
    with pytest.raises(machine.ReadingError, match="didn't answer"):
        machine.card_use()


@pytest.mark.skipif(sys.platform != "win32", reason="the places drivers install it on Windows")
def test_the_tool_is_found_where_drivers_put_it_before_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, program_files = tmp_path / "windows", tmp_path / "programs"
    monkeypatch.setenv("SystemRoot", str(system))
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setattr(machine.shutil, "which", lambda _name: "from-path")

    assert machine._smi() == "from-path"
    older = program_files / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
    older.parent.mkdir(parents=True)
    older.write_bytes(b"")
    assert machine._smi() == str(older)
    current = system / "System32" / "nvidia-smi.exe"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"")
    assert machine._smi() == str(current)


def test_the_computers_memory_is_read() -> None:
    assert machine.total_memory_mib() > 0


def test_memory_that_cant_be_read_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("not here")

    if sys.platform == "win32":
        monkeypatch.setattr(machine.ctypes, "WinDLL", fail)
    else:
        monkeypatch.setattr(machine.os, "sysconf", fail)
    with pytest.raises(machine.ReadingError, match="OSError"):
        machine.total_memory_mib()


def test_free_space_is_asked_of_a_folder_that_exists_and_none_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[Path] = []

    def usage(folder: Path) -> SimpleNamespace:
        asked.append(folder)
        return SimpleNamespace(total=0, used=0, free=5 * machine.MIB + machine.MIB - 1)

    monkeypatch.setattr(machine.shutil, "disk_usage", usage)
    assert machine.free_mib(tmp_path / "not" / "made" / "yet") == 5  # rounded down
    assert asked == [tmp_path]
    assert list(tmp_path.iterdir()) == []
