"""Application settings: layered YAML profiles + .env + environment overrides.

Resolution order, lowest to highest precedence:
    field defaults < configs/default.yaml < configs/profile.<name>.yaml
    < repo .env < process environment variables.

The active profile is chosen by VERONICA_PROFILE, from the environment or .env.
Nested values use the VERONICA_ prefix and "__" delimiter, e.g.
VERONICA_GPU__VRAM_BUDGET_GB targets gpu.vram_budget_gb.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.core.config_capabilities import CapabilitySettings
from app.core.config_llm import LlmSettings
from app.core.config_memory import MemorySettings
from app.core.config_voice import (
    AsrSettings,
    RealtimeSettings,
    TtsSettings,
    VadSettings,
    VoiceSettings,
)
from app.paths import INSTALL_ROOT, Place, anchored
from app.telemetry.settings import TelemetrySettings

_CONFIGS_DIR = INSTALL_ROOT / "configs"
_ENV_FILE = INSTALL_ROOT / ".env"
# The stores, and where each is kept in `data_dir` when no setting of its own says:
# (section, field, place under the data folder).
STORES = (
    ("memory", "db_path", "memory/memory.db"),
    ("voice", "voices_dir", "voices"),
    ("realtime", "metrics_path", "metrics/voice_turns.jsonl"),
    ("telemetry", "dir", "telemetry"),
)


class AppSettings(BaseModel):
    """Top-level application identity."""

    name: str = "Veronica"
    env: str = "dev"


class LoggingSettings(BaseModel):
    """Logging configuration."""

    level: str = "INFO"


class GpuSettings(BaseModel):
    """VRAM budget for the GPU model manager, in decimal GB (`gpu_manager.gib`)."""

    vram_budget_gb: float = 16.0


def _load_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML mapping into a dict; empty dict if the file is absent."""
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a top-level mapping")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base; override wins for scalars and lists."""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


class _ProfileChoice(BaseSettings):
    # VERONICA_PROFILE on its own, read like every other setting (environment, then
    # .env), because it has to be known before the YAML it selects is loaded.
    model_config = SettingsConfigDict(env_prefix="VERONICA_", extra="ignore")
    profile: str = "default"


def _active_profile() -> str:
    """Profile name from VERONICA_PROFILE (selects configs/profile.<name>.yaml)."""
    return _ProfileChoice(_env_file=_ENV_FILE).profile


def _layered_yaml() -> dict[str, Any]:
    """Merge default.yaml with the active profile's YAML (profile wins)."""
    data = _load_yaml(_CONFIGS_DIR / "default.yaml")
    profile = _active_profile()
    if profile != "default":
        data = _deep_merge(data, _load_yaml(_CONFIGS_DIR / f"profile.{profile}.yaml"))
    data["profile"] = profile
    return data


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Lowest-priority settings source backed by the layered YAML config."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data = _layered_yaml()

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        # Unused: __call__ returns the whole mapping, so per-field lookup is a no-op.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._data


class Settings(BaseSettings):
    """Resolved application settings."""

    model_config = SettingsConfigDict(
        env_prefix="VERONICA_",
        env_nested_delimiter="__",
        env_file=str(_ENV_FILE),
        extra="ignore",
    )

    app: AppSettings = AppSettings()
    logging: LoggingSettings = LoggingSettings()
    gpu: GpuSettings = GpuSettings()
    asr: AsrSettings = AsrSettings()
    llm: LlmSettings = LlmSettings()
    tts: TtsSettings = TtsSettings()
    vad: VadSettings = VadSettings()
    voice: VoiceSettings = VoiceSettings()
    realtime: RealtimeSettings = RealtimeSettings()
    memory: MemorySettings = MemorySettings()
    capabilities: CapabilitySettings = CapabilitySettings()
    telemetry: TelemetrySettings = TelemetrySettings()
    profile: str = "default"
    # All of her data: the personality, the capability policy and attachments, and every
    # store that has no location of its own set (memory.db, voices, telemetry, call
    # metrics; `STORES`). Made private to the account she runs as at every start
    # (`core/data_acl.py`), so moving it moves, and keeps private, all of it.
    data_dir: Place = "data"

    @model_validator(mode="after")
    def _stores_in_the_data_folder(self) -> Settings:
        """A store with no location of its own set is kept in `data_dir`. One that is set,
        from YAML, .env or the environment, stays where it says.

        The place goes on a copy of the section, written past pydantic so it isn't
        marked as set: a section handed in is left as it was, and the same section in
        other settings goes to their data folder. `model_copy` runs no validator, so
        settings with another `data_dir` are built, never copied.
        """
        root = self.data_path()
        for section, field, place in STORES:
            store = getattr(self, section)
            if field not in store.model_fields_set:
                placed = store.model_copy()
                object.__setattr__(placed, field, str(root / place))
                object.__setattr__(self, section, placed)
        return self

    def data_path(self) -> Path:
        """`data_dir` resolved: a relative path hangs off the installation, like every
        data location."""
        return anchored(self.data_dir)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Order sources high to low: init, env, .env, then layered YAML."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSettingsSource(settings_cls),
        )


@functools.lru_cache
def get_settings() -> Settings:
    """Return the cached singleton Settings instance."""
    return Settings()
