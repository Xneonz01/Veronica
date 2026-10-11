"""Secret scrubbing on the telemetry write path."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from app.telemetry import redact as redact_module
from app.telemetry.redact import PLACEHOLDER, clip_text, redact, redact_value


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnop1234",
        "failed with bearer abcdefghijklmnop1234",
        "key sk-" + "a1b2c3d4e5f6g7h8i9j0",
        "token ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2",
        "id AKIA" + "ABCDEFGHIJKLMNOP",
        "jwt eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl",
        "password=hunter2",
        'client_secret: "xyz789"',
        "https://admin:hunter2@10.0.0.5/api",
        "digest " + "0123456789abcdef" * 3,
        "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----",
    ],
)
def test_known_secret_shapes_are_replaced(text: str) -> None:
    out = redact(text, 4000)
    assert PLACEHOLDER in out
    for secret in ("hunter2", "xyz789", "a1b2c3d4e5f6", "ABCDEFGHIJKLMNOP", "MIIE"):
        assert secret not in out


def test_urlsafe_tokens_are_caught() -> None:
    token = secrets.token_urlsafe(32)
    # token_urlsafe can, rarely, produce a run with no digit or no capital; the rule
    # needs all three character classes, so build a token that has them.
    token = "Ab1" + token
    assert token not in redact(f"session {token}", 4000)


@pytest.mark.parametrize(
    "text",
    [
        "token_count=128 prompt_tokens=42 tokenizer=qwen",
        "the run 20260924_101500_1a2b3c finished",
        r"loaded C:\Program Files\Veronica\models\whisper",
        "keyword monkey keyboard",
    ],
)
def test_ordinary_text_survives(text: str) -> None:
    assert redact(text, 4000) == text


def test_field_names_that_mean_secret_are_redacted_whatever_the_value() -> None:
    out = redact_value(
        {"password": "x", "api_key": "y", "session_token": "z", "token_count": 3, "count": 4},
        200,
    )
    assert out["password"] == out["api_key"] == out["session_token"] == PLACEHOLDER
    assert out["token_count"] == 3 and out["count"] == 4


def test_nested_values_keep_their_types() -> None:
    out = redact_value({"a": [1, 2.5, True, None, "password=p"], "b": ("x",)}, 200)
    assert out["a"][:4] == [1, 2.5, True, None]
    assert PLACEHOLDER in out["a"][4]
    assert out["b"] == ["x"]


def test_unknown_objects_become_strings() -> None:
    out = redact_value({"obj": object()}, 200)
    assert isinstance(out["obj"], str)


def test_account_names_in_paths_are_hidden() -> None:
    out = redact(r'File "C:\Users\someone\project\app.py", line 3', 4000)
    assert "someone" not in out and "<user>" in out
    assert "someone" not in redact("/home/someone/project/app.py", 4000)
    home = Path.home()
    assert redact(str(home / "notes.txt"), 4000).startswith("~")


def test_install_folder_is_hidden_in_tracebacks() -> None:
    root = Path(redact_module.__file__).resolve().parents[3]
    source = root / "backend" / "app" / "main.py"
    for shown, sep in ((str(source), os.sep), (source.as_posix(), "/")):
        expected = sep.join(["<veronica>", "backend", "app", "main.py"])
        assert redact(f'File "{shown}", line 9', 4000) == f'File "{expected}", line 9'


def test_clipping_marks_the_cut() -> None:
    assert clip_text("abcdef", 4) == "a…ef"
    assert clip_text("abc", 4) == "abc"
    assert clip_text("abc", 0) == ""


def test_a_clipped_traceback_keeps_the_line_that_names_the_error() -> None:
    # Clipping used to keep only the start, which for a long traceback is the outermost
    # frames: the failing line and the exception at the bottom were cut off.
    frames = "".join(f'  File "app/module_{n}.py", line {n}, in step\n' for n in range(200))
    text = "Traceback (most recent call last):\n" + frames + "ValueError: the real reason"
    out = redact(text, 2000)
    assert len(out) == 2000
    assert out.startswith("Traceback") and out.endswith("ValueError: the real reason")
