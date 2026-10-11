"""Hold the token estimate against a model's own count, text by text.

    uv run python -m scripts.token_probe
    uv run python -m scripts.token_probe --models qwen3.5:9b,qwen3:8b

Sends each text in `eval/tokens.py` alone as the user message, with a window far
larger than it and one token to generate; its count is the prompt's minus that of an
empty message. Prints the count beside the estimate (`adapters/llm/tokens.py`). A ratio
below 1.00 means the estimate undercounts that model: the chat window would then let a
prompt through that the server cuts, so retune the estimate before relying on it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.adapters.llm.tokens import estimate
from app.core.config import get_settings
from eval.tokens import samples

# Room enough that the server can't be the one truncating what it's asked to count.
_MEASURING_WINDOW = 32768


async def _count(llm: OllamaLLMClient, model: str, text: str) -> int | None:
    usage = None
    async for chunk in llm.stream_chat([{"role": "user", "content": text}], None, model=model):
        if chunk.done:
            usage = chunk.usage
    return usage.prompt_tokens if usage is not None else None


async def measure(models: list[str]) -> None:
    # Each measurement generates one token on purpose; the client's warning about a
    # reply cut short at its limit would only be noise here.
    logging.getLogger("app.adapters.llm.ollama_client").setLevel(logging.ERROR)
    settings = get_settings()
    llm = OllamaLLMClient(
        settings.llm.model_copy(update={"num_ctx": _MEASURING_WINDOW, "num_predict": 1})
    )
    texts = samples()
    try:
        for model in models:
            empty = await _count(llm, model, "")
            if empty is None:
                print(f"{model}: the server reported no counts")
                continue
            print(f"{model}\n{'text':20}{'counted':>9}{'estimate':>10}{'ratio':>7}")
            counted: dict[str, int] = {}
            for name, text in texts.items():
                tokens = await _count(llm, model, text)
                if tokens is None:
                    continue
                counted[name] = tokens - empty
                guess = estimate(text)
                flag = "  UNDER" if guess < counted[name] else ""
                print(f"{name:20}{counted[name]:>9}{guess:>10}{guess / counted[name]:>7.2f}{flag}")
            print(f"MEASURED = {counted}")
            # Loaded with a window four times the product's, it would otherwise stay
            # resident at that size and crowd out a call started after this.
            await llm.release_model(model)
    finally:
        await llm.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", default=get_settings().llm.model, help="comma-separated")
    args = parser.parse_args()
    asyncio.run(measure([model for model in args.models.split(",") if model]))


if __name__ == "__main__":
    main()
