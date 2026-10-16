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
    # The web (ADR 0003): whether it is used, and through which provider, is set live in
    # Settings (`services/connected/`); these bound what one request may cost.
    web_results: int = Field(default=5, ge=1, le=20)
    web_max_page_bytes: int = Field(default=2_000_000, ge=10_000)
    web_timeout_s: float = Field(default=10.0, gt=0)
    # How often a capability may run (`kernel/budget.py`): a page asking her to read
    # fifty more is refused past these. Per turn, by capability, and every call of a turn.
    turn_budget: dict[str, int] = Field(default_factory=lambda: {"web.search": 4, "web.read": 6})
    turn_total: int = Field(default=12, ge=1)
    minute_budget: dict[str, int] = Field(
        default_factory=lambda: {"web.search": 15, "web.read": 30}
    )
    # Tool servers (MCP, `services/mcp`): which run is set live in Settings; these bound
    # them. Every offered tool is read by the model on every chat turn.
    mcp_tools_per_turn: int = Field(default=12, ge=1, le=64)
    # And what they may take of the chat window together, in tokens. Every chat turn
    # sends the persona and every tool before anything else, so these come out of the
    # room for the message, what she recalls for it and the conversation: 3,637 tokens
    # at the default window with web search on, 2,137 with this much taken. One tool
    # with long descriptions measured 1,752. Raise it with `llm.num_ctx`.
    mcp_tool_tokens: int = Field(default=1_500, ge=0)
    mcp_call_timeout_s: float = Field(default=60.0, gt=0)
