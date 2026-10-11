"""The architecture as tests: rules a change must not break without saying so.

Each rule is one the codebase is built on (see CLAUDE.md and ARCHITECTURE.md). A port
or a refactor that breaks one fails here with the offending file named, instead of
surfacing in a review weeks later. An exception to a rule is listed next to the rule,
with its reason, so it stays a decision rather than drift.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.capabilities import register_capabilities
from app.core.config import get_settings
from app.kernel.registry import CapabilityRegistry
from app.services.tools.broker import ToolBroker

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
REPO = BACKEND.parent

# Model and ML runtime packages. The adapters own the models; everything else goes
# through them.
_MODEL_PACKAGES = frozenset(
    {
        "chatterbox",
        "ctranslate2",
        "faster_whisper",
        "huggingface_hub",
        "onnxruntime",
        "sentence_transformers",
        "silero_vad",
        "tokenizers",
        "torch",
        "torchaudio",
        "transformers",
    }
)
_ALLOWED_OUTSIDE_ADAPTERS = {
    # Reads VRAM for the budget; it never builds a model.
    "app/core/gpu_manager.py": {"torch"},
    # Silero VAD is a small CPU model owned by the call loop; it never touches the GPU.
    "app/services/voice/turn.py": {"silero_vad", "torch"},
    # Resamples a reference clip: signal processing, not a model.
    "app/services/voice/voice_studio.py": {"torch", "torchaudio"},
}
_MODEL_CONSTRUCTORS = frozenset({"WhisperModel", "SentenceTransformer", "CrossEncoder"})


def _modules(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND).as_posix()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _outside_adapters() -> Iterator[tuple[str, ast.Module]]:
    for path in _modules(APP):
        rel = _rel(path)
        if not rel.startswith("app/adapters/"):
            yield rel, _tree(path)


def _imported_packages(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def _called_names(tree: ast.Module) -> Iterator[tuple[str, ast.Call]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                yield node.func.id, node
            elif isinstance(node.func, ast.Attribute):
                yield node.func.attr, node


def test_model_libraries_are_imported_only_by_the_adapters() -> None:
    offenders = []
    for rel, tree in _outside_adapters():
        extra = (_imported_packages(tree) & _MODEL_PACKAGES) - _ALLOWED_OUTSIDE_ADAPTERS.get(
            rel, set()
        )
        if extra:
            offenders.append(f"{rel}: {sorted(extra)}")
    assert not offenders, f"model libraries imported outside app/adapters: {offenders}"


def test_models_are_built_only_inside_the_adapters() -> None:
    offenders = [
        f"{rel}:{call.lineno} {name}"
        for rel, tree in _outside_adapters()
        for name, call in _called_names(tree)
        if name in _MODEL_CONSTRUCTORS or name == "from_pretrained"
    ]
    assert not offenders, f"models built outside app/adapters: {offenders}"


def test_gpu_models_load_only_through_the_gpu_manager() -> None:
    # An adapter's `*_loader()` builds the thing the GPU manager loads. Outside the
    # adapters it may only be handed straight to `manager.register(...)`; calling it
    # anywhere else would load a model the manager can't account for or evict.
    loaders = {
        node.name
        for path in _modules(APP / "adapters")
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.FunctionDef) and node.name.endswith("_loader")
    }
    assert loaders, "expected the adapters to define model loaders"
    offenders = []
    for rel, tree in _outside_adapters():
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for name, call in _called_names(tree):
            if name not in loaders:
                continue
            parent = parents.get(call)
            handed_to_manager = (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Attribute)
                and parent.func.attr == "register"
            )
            if not handed_to_manager:
                offenders.append(f"{rel}:{call.lineno} calls {name} outside manager.register")
    assert not offenders, f"GPU model loaders used outside the GPU manager: {offenders}"


def test_capability_handlers_run_only_inside_the_kernel() -> None:
    # Policy, confirmation and the audit row all live in the kernel; a handler called
    # anywhere else acts on the machine with none of them.
    # Matches `capability.handler(...)`; a bare `handler(event)` is an event-bus
    # subscriber, not a capability.
    offenders = [
        f"{_rel(path)}:{call.lineno}"
        for path in _modules(APP)
        if _rel(path) != "app/kernel/kernel.py"
        for name, call in _called_names(_tree(path))
        if name == "handler" and isinstance(call.func, ast.Attribute)
    ]
    assert not offenders, f"capability handlers called outside the kernel: {offenders}"


def test_every_tool_the_model_is_offered_is_a_capability() -> None:
    # Memory included: its tools had a path of their own beside the kernel until
    # 0.17, with no policy and no audit row.
    registry = CapabilityRegistry()
    register_capabilities(registry, get_settings())
    broker = ToolBroker(None, registry, actor="architecture-test")  # type: ignore[arg-type]
    offered = {schema["function"]["name"] for schema in broker.schemas()}
    assert offered == {capability.tool_name for capability in registry}
    assert {"memory_save", "memory_recall", "memory_correct", "memory_forget"} <= offered


def test_library_code_logs_instead_of_printing() -> None:
    offenders = [
        f"{_rel(path)}:{call.lineno}"
        for path in _modules(APP)
        for name, call in _called_names(_tree(path))
        if name == "print" and isinstance(call.func, ast.Name)
    ]
    assert not offenders, f"print() in app code: {offenders}"


def _same_function(node: ast.AST) -> Iterator[ast.AST]:
    """Walk a function body without descending into nested functions or lambdas."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _same_function(child)


def test_async_code_never_blocks_the_event_loop_with_sleep() -> None:
    offenders = []
    for path in _modules(APP):
        for function in ast.walk(_tree(path)):
            if not isinstance(function, ast.AsyncFunctionDef):
                continue
            for node in _same_function(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "sleep"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "time"
                ):
                    offenders.append(f"{_rel(path)}:{node.lineno} in {function.name}")
    assert not offenders, f"time.sleep inside async functions: {offenders}"


@pytest.mark.parametrize(
    ("folder", "limit"),
    [
        ("backend/app", 300),
        ("backend/eval", 300),
        ("backend/scripts", 300),
        ("backend/tests", 500),
        ("frontend/src", 500),
        ("frontend/scripts", 500),
        ("frontend/src-tauri/src", 500),
    ],
)
def test_files_stay_within_the_size_limit(folder: str, limit: int) -> None:
    # 300 lines is the aim everywhere and the rule for backend code; 500 is the hard
    # limit for tests and the front end. Stylesheets count: a 500-line CSS module is
    # a god-file too.
    too_long = []
    for path in sorted((REPO / folder).rglob("*")):
        if path.suffix not in {".py", ".ts", ".tsx", ".rs", ".css"} or "__pycache__" in path.parts:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > limit:
            too_long.append(f"{path.relative_to(REPO).as_posix()}: {lines}")
    assert not too_long, f"over {limit} lines: {too_long}"
