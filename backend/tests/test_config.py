"""Config loading: defaults, profile override, and .env / environment override."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core import config
from app.core.config import Settings, get_settings
from app.core.config_voice import RealtimeSettings, VadSettings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Ensure each test sees a freshly loaded Settings singleton."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_load() -> None:
    settings = get_settings()
    assert settings.app.name == "Veronica"
    assert settings.app.env == "dev"
    assert settings.gpu.vram_budget_gb == 16
    assert settings.profile == "default"


def test_profile_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERONICA_PROFILE", "gpu_16gb")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.profile == "gpu_16gb"
    assert settings.app.env == "desktop"  # supplied only by the profile YAML


def test_profile_set_in_dotenv_is_applied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The profile is resolved before the YAML it selects, and that step used to read only
    # the process environment, so a profile named in .env was reported but not applied.
    env_file = tmp_path / ".env"
    env_file.write_text("VERONICA_PROFILE=gpu_16gb\n", encoding="utf-8")
    monkeypatch.delenv("VERONICA_PROFILE", raising=False)
    monkeypatch.setattr(config, "_ENV_FILE", env_file)
    settings = Settings(_env_file=str(env_file))
    assert settings.profile == "gpu_16gb"
    assert settings.app.env == "desktop"  # supplied only by the profile YAML


def test_environment_beats_dotenv_for_the_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VERONICA_PROFILE=gpu_16gb\n", encoding="utf-8")
    monkeypatch.setenv("VERONICA_PROFILE", "default")
    monkeypatch.setattr(config, "_ENV_FILE", env_file)
    settings = Settings(_env_file=str(env_file))
    assert settings.profile == "default"
    assert settings.app.env == "dev"


def test_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERONICA_GPU__VRAM_BUDGET_GB", "24")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.gpu.vram_budget_gb == 24
    assert settings.llm.model == "qwen3.5:9b"  # the rest keeps its value


def test_dotenv_file_override(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VERONICA_GPU__VRAM_BUDGET_GB=48\n", encoding="utf-8")
    settings = Settings(_env_file=str(env_file))
    assert settings.gpu.vram_budget_gb == 48


@pytest.mark.parametrize(
    ("section", "values"),
    [
        # Each of these made the call deaf, cut every turn short or hung the event loop,
        # without a word, instead of being refused where the setting is read.
        (VadSettings, {"sample_rate": 44100}),
        (VadSettings, {"threshold": 1.5}),
        (VadSettings, {"turn_complete_threshold": -0.1}),
        (VadSettings, {"turn_complete_threshold": 1.2}),
        (VadSettings, {"end_silence_min_ms": 900, "end_silence_max_ms": 800}),
        (RealtimeSettings, {"chunk_max_chars": 0}),
        (RealtimeSettings, {"history_turns": -1}),
    ],
)
def test_a_voice_setting_that_cannot_work_is_refused(section: type, values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        section(**values)


def test_an_equal_floor_and_ceiling_is_a_fixed_wait() -> None:
    # Allowed on purpose: it is how the end-of-turn model is switched off.
    settings = VadSettings(end_silence_min_ms=400, end_silence_max_ms=400)
    assert settings.end_silence_min_ms == settings.end_silence_max_ms == 400
