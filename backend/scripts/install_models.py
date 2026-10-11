"""Put the model weights Veronica opens on disk. The one place the network is allowed.

    cd backend && uv run python -m scripts.install_models          # fetch what is missing
    cd backend && uv run python -m scripts.install_models --check  # say what is missing

The server never downloads: every loader opens its model from its folder on disk, and
`create_app` forces the hub offline as well. This script fetches whatever
`app/adapters/model_files.missing` reports, into the Hugging Face cache the server
reads, so the two can't drift. Run it once on a new machine and again when a model
changes. Nothing in `app/` runs it. Silero's voice detector ships inside its package
and needs nothing; the language model is Ollama's (`ollama pull`).

It turns `HF_HUB_OFFLINE` off before the hub is first imported, which is when the hub
reads it: the variable is the server's, and a shell that carried it over would stop the
fetch. Set to "0" rather than removed, because the hub falls back on
`TRANSFORMERS_OFFLINE` when it is absent.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

from app.adapters import model_files
from app.core.config import Settings, get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Put Veronica's model weights on disk.")
    parser.add_argument("--check", action="store_true", help="say what is missing; fetch nothing")
    args = parser.parse_args(argv)
    os.environ["HF_HUB_OFFLINE"] = "0"
    settings = get_settings()

    absent = model_files.missing(settings)
    if absent and not args.check:
        fetchers = fetchers_by_role(settings)
        for weights in absent:
            print(f"Fetching {weights.role} ({weights.name}) ...", flush=True)
            fetchers[weights.role]()
        absent = model_files.missing(settings)
    print(model_files.sentence(absent) if absent else "Every model is on disk.")
    return 1 if absent else 0


def fetchers_by_role(settings: Settings) -> dict[str, Callable[[], object]]:
    """How to fetch each of `model_files.required`, keyed by its role."""
    from huggingface_hub import hf_hub_download, snapshot_download

    size = settings.asr.model_size

    def embedder() -> object:
        # On the CPU: this only has to land the files it opens, and the GPU may be
        # holding a running server's models.
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(settings.memory.embedding_model, device="cpu")

    return {
        "speech recognition": lambda: snapshot_download(
            model_files.WHISPER_REPOS.get(size, size), allow_patterns=model_files.WHISPER_FILES
        ),
        "speech synthesis": lambda: snapshot_download(
            model_files.TURBO_REPO, allow_patterns=model_files.TURBO_FILES
        ),
        "end-of-turn detection": lambda: hf_hub_download(
            model_files.SMART_TURN_REPO, model_files.SMART_TURN_FILE
        ),
        "memory and documents": embedder,
        "document ranking": lambda: snapshot_download(
            model_files.RERANKER_REPO, allow_patterns=model_files.RERANKER_FILES
        ),
    }


if __name__ == "__main__":
    sys.exit(main())
