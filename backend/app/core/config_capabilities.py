"""What Veronica may touch, and how it is approved: a section of `config.Settings`
(`capabilities`), kept in its own file like `config_llm.py`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.paths import INSTALL_ROOT


class CapabilitySettings(BaseModel):
    """The capability kernel: what Veronica may touch, and how it is approved."""

    # Roots `files.*` may read or write inside. Narrow by default: only this repo,
    # until the user widens it. It is an absolute path rather than "." so the boundary
    # doesn't depend on the directory the server was started from (starting it from
    # C:\ would otherwise expose the whole drive).
    file_roots: list[str] = Field(default_factory=lambda: [str(INSTALL_ROOT)])
    max_read_bytes: int = 256_000
    max_write_bytes: int = 256_000
    search_budget_s: float = 3.0
    # At least one: at 0, every folder read as empty.
    search_max_results: int = Field(default=50, ge=1)
    # Applications `apps.open` may launch, by friendly name.
    app_allowlist: dict[str, str] = Field(
        default_factory=lambda: {"notepad": "notepad.exe", "explorer": "explorer.exe"}
    )
    # A guarded action waits this long for a human answer, then denies (fails closed).
    confirm_timeout_s: float = 45.0
    # capability name -> auto_allow | confirm | deny. Overrides the risk-tier default;
    # a `dangerous` capability can never be auto-allowed regardless of what is set here.
    policy: dict[str, str] = Field(default_factory=dict)
    # An argument copied from what she read makes the call ask first (`kernel/taint.py`)
    # once it is this many words: one word is too short to be anyone's sentence, and
    # "No allergies." is enough to change a memory that matters.
    taint_min_words: int = Field(default=2, ge=1)
