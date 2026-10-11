"""The model weights Veronica opens, from disk and only disk.

The loaders used to ask huggingface.co at every start whether their models had a newer
revision (bug 22), and the embedding model's loader asked once per file, which the first
chat message after a start waited on (bug 31). Nothing personal was sent, but a
local-first assistant shouldn't reach out at all, or need the network to start.

Offline twice over: every loader opens its model from its folder on disk, found here,
and `create_app` forces `HF_HUB_OFFLINE` as well. Given a folder, the libraries have no
name to ask the hub about; given a name, some of them asked whatever `local_files_only`
said (transformers checks whether a tokenizer comes from a Mistral model). A model that
isn't on disk is reported with the command that fetches it (`INSTALL`):
`scripts/install_models.py` is the one place with network access, and nothing in `app/`
runs it.

No lookup here imports a model library, so checking for a model can't change the order
their native code loads in: faster-whisper brings CTranslate2, which carries its own
cuDNN, and torch's has to be the one loaded first. Its table of model names is copied
below for that reason, and `tests/test_model_files.py` holds it to the original.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from app.core.config import Settings

INSTALL = "uv run python -m scripts.install_models"
# The memory model's code: the start warms it only when it is on disk (`app/main.py`),
# and the app has words for every code (`frontend/src/shell/problemCopy.ts`).
MEMORY_MODEL = "embedding"
# The models done without when missing, less well: a longer wait before she answers,
# plainer document ranking. Chat or calls can't work without any other (the app's words
# say the same).
DONE_WITHOUT = frozenset({"turn", "rerank"})

# faster-whisper 1.2.1's `utils._MODELS`: a size that isn't a key is a repository id,
# as faster-whisper itself reads one.
WHISPER_REPOS: dict[str, str] = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en",
    "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
# The files faster-whisper's `download_model` fetches.
WHISPER_FILES = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

# Chatterbox Turbo's repository, the files its `from_pretrained` fetches, and the ones
# `from_local` can't load without (its built-in voice, `conds.pt`, is optional).
TURBO_REPO = "ResembleAI/chatterbox-turbo"
TURBO_FILES = ["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"]
_TURBO_NEEDS = (
    "ve.safetensors",
    "t3_turbo_v1.safetensors",
    "s3gen_meanflow.safetensors",
    "vocab.json",
    "merges.txt",
)
# smart-turn's repository and the one file of it Veronica opens (`adapters/turn`).
SMART_TURN_REPO = "pipecat-ai/smart-turn-v3"
SMART_TURN_FILE = "smart-turn-v3.1-cpu.onnx"
# The cross-encoder that reranks document search (`adapters/rerank`), and the two files
# of its repository Veronica opens: its ONNX export and its tokenizer.
RERANKER_REPO = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANKER_MODEL = "onnx/model_O2.onnx"
RERANKER_TOKENIZER = "tokenizer.json"
RERANKER_FILES = [RERANKER_MODEL, RERANKER_TOKENIZER]
# What Whisper can't load without. Missing `tokenizer.json`, faster-whisper quietly
# fetches another model's tokenizer; `preprocessor_config.json` isn't required, since
# older models have none and faster-whisper's default is right for them.
_WHISPER_NEEDS = ("model.bin", "config.json", "tokenizer.json")
# What sentence-transformers can't load without, and the weights, in either format.
_EMBEDDER_NEEDS = ("modules.json", "config.json")
_EMBEDDER_WEIGHTS = ("model.safetensors", "pytorch_model.bin")


@dataclass(frozen=True, slots=True)
class Weights:
    """One model Veronica opens: what it is for, and how to find it on disk. `code`
    names the role for the app, which has its own words for it."""

    code: str
    role: str
    name: str
    find: Callable[[], object]  # raises when the files aren't on disk; never fetches


def required(settings: Settings) -> tuple[Weights, ...]:
    """Every model the loaders open from the Hugging Face cache. Silero ships in its
    package and needs nothing; the reranker is needed only while reranking is on."""
    size = settings.asr.model_size
    embedder = settings.memory.embedding_model
    models: tuple[Weights, ...] = (
        Weights("stt", "speech recognition", f"faster-whisper {size}", partial(whisper_path, size)),
        Weights("tts", "speech synthesis", "Chatterbox Turbo", turbo_path),
        Weights("turn", "end-of-turn detection", "smart-turn v3.1", smart_turn_path),
        Weights(MEMORY_MODEL, "memory and documents", embedder, partial(embedder_path, embedder)),
    )
    if settings.memory.rag_rerank_depth > 0:
        models += (Weights("rerank", "document ranking", "ms-marco MiniLM-L-6", reranker_path),)
    return models


def missing(settings: Settings) -> list[Weights]:
    """The models that aren't on disk, found without a byte of network traffic."""
    absent: list[Weights] = []
    for weights in required(settings):
        try:
            weights.find()
        except (OSError, ValueError):
            # Files that aren't there raise `LocalEntryNotFoundError` (a
            # `FileNotFoundError`), or `FileNotFoundError` from `_require`; a name that
            # can't be a repository raises the hub's `HFValidationError` (a `ValueError`).
            absent.append(weights)
    return absent


def sentence(absent: list[Weights]) -> str:
    """Which models are missing, and the command that installs them."""
    named = "; ".join(f"{weights.role} ({weights.name})" for weights in absent)
    return f"Not on disk: {named}. Install them with `{INSTALL}` from backend/."


def whisper_path(size: str) -> str:
    """The configured Whisper model's folder: a local folder as given, else the cache's."""
    if Path(size).is_dir():
        return size
    from huggingface_hub import snapshot_download

    folder = snapshot_download(
        WHISPER_REPOS.get(size, size), allow_patterns=WHISPER_FILES, local_files_only=True
    )
    _require(Path(folder), _WHISPER_NEEDS)
    return folder


def turbo_path() -> str:
    """Chatterbox Turbo's snapshot, checked for the files `from_local` opens."""
    from huggingface_hub import snapshot_download

    folder = snapshot_download(TURBO_REPO, allow_patterns=TURBO_FILES, local_files_only=True)
    _require(Path(folder), _TURBO_NEEDS)
    return folder


def smart_turn_path() -> str:
    """smart-turn v3.1's model file in the cache."""
    from huggingface_hub import hf_hub_download

    return hf_hub_download(SMART_TURN_REPO, SMART_TURN_FILE, local_files_only=True)


def reranker_path() -> str:
    """The cross-encoder's snapshot, checked for the model and its tokenizer."""
    from huggingface_hub import snapshot_download

    folder = snapshot_download(RERANKER_REPO, allow_patterns=RERANKER_FILES, local_files_only=True)
    _require(Path(folder), tuple(RERANKER_FILES))
    return folder


def embedder_path(name: str) -> str:
    """The embedding model's folder: a local folder as given, else the cache's.

    Checked for its weights, which sentence-transformers fetches last: an install cut
    off before them left `modules.json` behind, and the model reported on disk.
    """
    if Path(name).is_dir():
        return name
    from huggingface_hub import snapshot_download

    folder = Path(snapshot_download(name, local_files_only=True))
    _require(folder, _EMBEDDER_NEEDS)
    if not any((folder / weights).is_file() for weights in _EMBEDDER_WEIGHTS):
        raise FileNotFoundError(f"{folder.name} has no weights")
    return str(folder)


def _require(folder: Path, names: tuple[str, ...]) -> None:
    # A snapshot can exist with files missing from it, after an interrupted download.
    absent = [name for name in names if not (folder / name).is_file()]
    if absent:
        raise FileNotFoundError(f"{folder.name} is missing {', '.join(absent)}")
