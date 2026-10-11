"""Measure each lane's largest prompt on the server, to size `LlmSettings.num_ctx`.

    uv run python -m scripts.prompt_probe
    uv run python -m scripts.prompt_probe --models qwen3.5:9b,qwen3:8b

Sends the prompts from `eval/window.py` with a window far larger than they are, so
nothing is cut, and one token to generate, then prints Ollama's `prompt_eval_count`
beside the characters it was given. The characters-per-token it reports is what
`tests/test_llm.py` estimates the live prompt with; set it from the lowest reading,
so the estimate never comes out under the server's count.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.core.config import get_settings
from eval import window

# Room enough that the server can't be the one truncating what it's asked to count.
_MEASURING_WINDOW = 32768


async def measure(models: list[str]) -> None:
    # Each measurement generates one token on purpose; the client's warning about a
    # reply cut short at its limit would only be noise here.
    logging.getLogger("app.adapters.llm.ollama_client").setLevel(logging.ERROR)
    settings = get_settings()
    llm = OllamaLLMClient(
        settings.llm.model_copy(update={"num_ctx": _MEASURING_WINDOW, "num_predict": 1})
    )
    prompts = window.largest(settings)
    try:
        print(f"{'model':14}{'lane':7}{'characters':>12}{'tokens':>9}{'chars/token':>13}")
        for model in models:
            for lane, (messages, tools) in prompts.items():
                usage = None
                async for chunk in llm.stream_chat(messages, tools, model=model):
                    if chunk.done:
                        usage = chunk.usage
                characters = window.characters((messages, tools))
                if usage is None:
                    print(f"{model:14}{lane:7}{characters:>12}{'n/a':>9}")
                    continue
                ratio = characters / usage.prompt_tokens
                print(f"{model:14}{lane:7}{characters:>12}{usage.prompt_tokens:>9}{ratio:>13.2f}")
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
