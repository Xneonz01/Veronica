"""The language model's settings: which model, where Ollama is, and what each
request sends it. A section of `config.Settings` (`llm`), kept in its own file.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

#: What a reply is given of the window when `num_predict` sets no limit.
UNLIMITED_REPLY_TOKENS = 2048
#: The least the window must leave the prompt beside the reply: the longest persona,
#: every tool, memory and documents at their budgets, and a message, with web search on
#: (its rules and tools; 5,632 before 0.35.0, which held them without it). The chat
#: window estimates tokens from above (`adapters/llm/tokens.py`); `tests/test_llm.py`
#: holds this to that estimate.
MIN_PROMPT_TOKENS = 5888


class LlmSettings(BaseModel):
    """Streaming LLM (Ollama native /api/chat, tool-calling) settings."""

    # An address: "localhost" resolves to IPv6 first while Ollama listens on IPv4, and
    # each fresh connection waited out a ~250 ms fallback.
    base_url: str = "http://127.0.0.1:11434"
    # One model for chat, the live call and learning (ARCHITECTURE decision 3): two
    # can't both stay loaded beside the speech models in 16 GB.
    model: str = "qwen3.5:9b"
    temperature: float = 0.7
    request_timeout_s: float = 60.0  # write/pool budget
    connect_timeout_s: float = 5.0  # local server: up or not, decide fast
    # Read timeout, covering a cold model load before the first byte. Not longer: on a
    # contended GPU saying promptly that she can't answer beats minutes of "Thinking".
    load_timeout_s: float = 120.0
    think: bool = False  # qwen3 reasons by default; off for low-latency voice
    keep_alive: str = "30m"  # pin the model in VRAM between turns
    # Loaded in the background at start, beside the memory model. Nothing else loaded it
    # before the first message or a call: a first message sent at launch waited 21 s,
    # 16 of them for the model, and chat seemed to need a call first (a call's warm-up
    # loaded it). Off in the tests.
    warm_on_start: bool = True
    # Sent, not left at Ollama's 4,096, which drops the front of a long prompt (the
    # persona, the tool rules) without an error. The largest prompt, chat's with web
    # search on, measures 5,691 tokens (`scripts/prompt_probe.py`, 0.35.0); with the
    # longest reply beside it 7,739, inside 8,192. The cache is VRAM the speech models
    # need. `tests/test_llm.py` fails if the prompt outgrows it. A larger model on a
    # larger card can be given more.
    num_ctx: int = Field(default=8192, ge=MIN_PROMPT_TOKENS)
    # The runaway guard: a reply that reaches it is logged as cut short. 0: no limit.
    num_predict: int = Field(default=2048, ge=0)

    @property
    def reply_tokens(self) -> int:
        """What the longest reply takes of the window, held back from every prompt."""
        return self.num_predict or UNLIMITED_REPLY_TOKENS

    @model_validator(mode="after")
    def _room_for_the_prompt(self) -> LlmSettings:
        # Checked here rather than at the first message: a window this small would
        # refuse every one of them.
        if self.num_ctx - self.reply_tokens < MIN_PROMPT_TOKENS:
            raise ValueError(
                f"num_ctx ({self.num_ctx}) must leave {MIN_PROMPT_TOKENS} tokens beside the "
                f"longest reply ({self.reply_tokens}) for her persona, tools and a message"
            )
        return self
