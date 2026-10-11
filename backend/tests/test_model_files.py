"""Models open from disk only, and a missing one is named with the command that installs it.

Bugs 22 and 31: the loaders asked huggingface.co about their models at every start.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters import model_files
from app.adapters.embeddings.e5_embedder import embedding_loader
from app.adapters.rerank.cross_encoder import open_reranker
from app.adapters.stt.faster_whisper_stream import whisper_loader
from app.adapters.tts.chatterbox_stream import chatterbox_loader
from app.adapters.turn.smart_turn import open_smart_turn
from app.core.config import AsrSettings, Settings, TtsSettings
from app.core.config_memory import MemorySettings
from app.main import create_app
from app.services.voice.realtime.lifecycle import _CALL_NEEDS
from scripts.install_models import fetchers_by_role

# The real check, not the one every other test sees (`conftest._models_on_disk`).
pytestmark = pytest.mark.model_files

BACKEND = Path(__file__).resolve().parents[1]


def _source(package: str, module: str) -> ast.Module:
    """A library module's source, parsed without importing it (see `model_files`)."""
    spec = importlib.util.find_spec(package)
    assert spec is not None and spec.origin is not None
    return ast.parse((Path(spec.origin).parent / module).read_text(encoding="utf-8"))


def _constant(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def _allow_patterns(tree: ast.Module, function: str) -> Any:
    """The `allow_patterns` a library function fetches with, a variable or an argument."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for inner in ast.walk(node):
                if isinstance(inner, ast.keyword) and inner.arg == "allow_patterns":
                    if isinstance(inner.value, ast.List):
                        return ast.literal_eval(inner.value)
                if isinstance(inner, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "allow_patterns"
                    for target in inner.targets
                ):
                    return ast.literal_eval(inner.value)
    raise AssertionError(f"no allow_patterns in {function}")


def test_the_copied_names_match_the_libraries() -> None:
    whisper = _source("faster_whisper", "utils.py")
    assert model_files.WHISPER_REPOS == _constant(whisper, "_MODELS")
    assert model_files.WHISPER_FILES == _allow_patterns(whisper, "download_model")
    turbo = _source("chatterbox", "tts_turbo.py")
    assert model_files.TURBO_REPO == _constant(turbo, "REPO_ID")
    assert model_files.TURBO_FILES == _allow_patterns(turbo, "from_pretrained")


def test_with_nothing_on_disk_every_model_is_named_with_the_install_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from huggingface_hub import constants

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    absent = model_files.missing(Settings())

    assert [weights.role for weights in absent] == [
        "speech recognition",
        "speech synthesis",
        "end-of-turn detection",
        "memory and documents",
        "document ranking",
    ]
    message = model_files.sentence(absent)
    assert "faster-whisper large-v3-turbo" in message and "Chatterbox Turbo" in message
    assert "smart-turn v3.1" in message and "ms-marco MiniLM-L-6" in message
    assert model_files.INSTALL in message


def test_with_reranking_off_its_model_is_not_needed() -> None:
    settings = Settings()
    settings.memory.rag_rerank_depth = 0

    assert "document ranking" not in [weights.role for weights in model_files.required(settings)]


def _snapshot(cache: Path, repo: str, names: tuple[str, ...]) -> None:
    """A repository as the hub's cache keeps it: a ref naming a snapshot, and its files."""
    folder = cache / f"models--{repo.replace('/', '--')}"
    (folder / "refs").mkdir(parents=True)
    (folder / "refs" / "main").write_text("0123abcd", encoding="utf-8")
    snapshot = folder / "snapshots" / "0123abcd"
    snapshot.mkdir(parents=True)
    for name in names:
        (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
        (snapshot / name).write_bytes(b"")


def test_with_every_file_on_disk_nothing_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The other half of the check: one that always said "missing" passed every test.
    from huggingface_hub import constants

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    settings = Settings()
    whisper = model_files.WHISPER_REPOS[settings.asr.model_size]
    _snapshot(tmp_path, whisper, model_files._WHISPER_NEEDS)
    _snapshot(tmp_path, model_files.TURBO_REPO, model_files._TURBO_NEEDS)
    _snapshot(tmp_path, model_files.SMART_TURN_REPO, (model_files.SMART_TURN_FILE,))
    embedder = (*model_files._EMBEDDER_NEEDS, "model.safetensors")
    _snapshot(tmp_path, settings.memory.embedding_model, embedder)
    _snapshot(tmp_path, model_files.RERANKER_REPO, tuple(model_files.RERANKER_FILES))

    assert model_files.missing(settings) == []


def test_a_reranker_without_its_tokenizer_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from huggingface_hub import constants

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, model_files.RERANKER_REPO, (model_files.RERANKER_MODEL,))

    with pytest.raises(FileNotFoundError, match="tokenizer.json"):
        model_files.reranker_path()


def test_a_snapshot_short_of_a_file_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # What an interrupted download leaves: the snapshot exists, a weight file doesn't.
    from huggingface_hub import constants

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, model_files.TURBO_REPO, ("ve.safetensors",))

    with pytest.raises(FileNotFoundError, match="t3_turbo_v1.safetensors"):
        model_files.turbo_path()
    assert "speech synthesis" in [weights.role for weights in model_files.missing(Settings())]


def test_an_embedder_cut_off_before_its_weights_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # sentence-transformers fetches its weights last; an install stopped before them left
    # `modules.json`, and the check that read only that said the model was installed.
    from huggingface_hub import constants

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    embedder = Settings().memory.embedding_model
    _snapshot(tmp_path, embedder, model_files._EMBEDDER_NEEDS)

    with pytest.raises(FileNotFoundError, match="no weights"):
        model_files.embedder_path(embedder)
    assert "memory and documents" in [weights.role for weights in model_files.missing(Settings())]


def test_the_loaders_open_their_models_from_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: dict[str, Any] = {}

    def whisper(path: str, **_kwargs: Any) -> None:
        opened["whisper"] = path

    def turbo(path: str, _device: str) -> SimpleNamespace:
        opened["turbo"] = path
        return SimpleNamespace()  # no `t3`: the stop hook leaves it alone

    def sentence_model(folder: str, **_kwargs: Any) -> None:
        opened["embedder"] = folder

    def onnx_session(path: str, **_kwargs: Any) -> None:
        opened.setdefault("onnx", []).append(Path(path))

    class Tokenizer:
        @staticmethod
        def from_file(path: str) -> SimpleNamespace:
            opened["tokenizer"] = Path(path)
            # Cut from the longer side: "only the passage" raises once a pasted question
            # alone fills the window.
            return SimpleNamespace(
                enable_truncation=lambda **options: opened.update(truncation=options),
                enable_padding=lambda **_: None,
                token_to_id=lambda _token: 0,
            )

    onnxruntime = SimpleNamespace(
        SessionOptions=SimpleNamespace,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0),
        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL=99),
        InferenceSession=onnx_session,
    )

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper))
    monkeypatch.setitem(
        sys.modules,
        "chatterbox.tts_turbo",
        SimpleNamespace(ChatterboxTurboTTS=SimpleNamespace(from_local=turbo)),
    )
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=sentence_model)
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", onnxruntime)
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(Tokenizer=Tokenizer))
    monkeypatch.setitem(
        sys.modules, "transformers", SimpleNamespace(WhisperFeatureExtractor=SimpleNamespace)
    )
    monkeypatch.setattr(model_files, "whisper_path", lambda size: f"disk/{size}")
    monkeypatch.setattr(model_files, "turbo_path", lambda: "disk/turbo")
    monkeypatch.setattr(model_files, "embedder_path", lambda name: f"disk/{name}")
    monkeypatch.setattr(model_files, "smart_turn_path", lambda: "disk/smart-turn")
    monkeypatch.setattr(model_files, "reranker_path", lambda: "disk/reranker")

    whisper_loader(AsrSettings())()
    chatterbox_loader(TtsSettings())()
    embedding_loader(MemorySettings())()
    open_smart_turn()
    open_reranker()

    assert opened == {
        "whisper": "disk/large-v3-turbo",
        "turbo": "disk/turbo",
        "embedder": "disk/intfloat/multilingual-e5-base",
        "onnx": [Path("disk/smart-turn"), Path("disk/reranker/onnx/model_O2.onnx")],
        "tokenizer": Path("disk/reranker/tokenizer.json"),
        "truncation": {"max_length": 512, "strategy": "longest_first"},
    }


def test_a_missing_model_is_named_at_start_up(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    absent = [model_files.Weights("tts", "speech synthesis", "Chatterbox Turbo", lambda: None)]
    monkeypatch.setattr(model_files, "missing", lambda _settings: absent)
    with TestClient(create_app(), base_url="http://localhost") as client:
        assert client.get("/health").status_code == 200
    assert "speech synthesis (Chatterbox Turbo)" in caplog.text
    assert model_files.INSTALL in caplog.text


def test_a_check_that_fails_does_not_stop_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only a report: chat works without the speech models, so it may not block a start.
    def broken(_settings: Settings) -> list[model_files.Weights]:
        raise RuntimeError("the cache can't be read")

    monkeypatch.setattr(model_files, "missing", broken)
    with TestClient(create_app(), base_url="http://localhost") as client:
        assert client.get("/health").status_code == 200


def test_the_app_forces_the_hub_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # a shell's setting doesn't reopen it
    create_app()
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_importing_the_app_leaves_the_hub_unimported() -> None:
    """The hub reads `HF_HUB_OFFLINE` when first imported, so that has to follow
    `create_app`. A fresh interpreter, because this one has imported it already."""
    code = "import sys, app.main; print('huggingface_hub' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def test_the_install_script_can_fetch_every_model_the_app_opens() -> None:
    settings = Settings()
    assert set(fetchers_by_role(settings)) == {
        weights.role for weights in model_files.required(settings)
    }


def test_a_call_has_words_for_every_model_it_can_name() -> None:
    """A call that can't start names the models it needs (`lifecycle._CALL_NEEDS`), in
    words its own copy holds (mutation: a model it names with no words there)."""
    copy = (BACKEND.parent / "frontend" / "src" / "audio" / "callErrors.ts").read_text(
        encoding="utf-8"
    )
    table = copy[copy.index("const CALL_MODELS = new Map([") :]
    table = table[: table.index("])")]
    assert set(re.findall(r"\['([a-z]+)', '", table)) == set(_CALL_NEEDS)


def test_the_app_has_words_for_every_model_code_and_the_same_install_command() -> None:
    """The notice names a missing model by what its code is for, and says the command
    that installs it: both written in the frontend, which can drift (mutation: a new
    model with no words there)."""
    copy = (BACKEND.parent / "frontend" / "src" / "shell" / "problemCopy.ts").read_text(
        encoding="utf-8"
    )
    codes = {weights.code for weights in model_files.required(Settings())}
    assert codes == {"stt", "tts", "turn", model_files.MEMORY_MODEL, "rerank"}
    for code in codes:
        assert f"['{code}', '" in copy, code
    assert f"INSTALL = '{model_files.INSTALL}'" in copy
