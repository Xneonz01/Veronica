"""Ollama's list of installed models (`/api/tags`), read as 0.33.3 answers it, and the
name a request for a model resolves to."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.adapters.llm.ollama_errors import OllamaUnreachableError
from app.adapters.llm.ollama_models import (
    InstalledModel,
    find,
    installed_models,
    loaded_models,
    on_this_computer,
    same_model,
)
from app.core.config_llm import LlmSettings

# Trimmed from what Ollama 0.33.3 answered (measured).
TAGS = {
    "models": [
        {
            "name": "qwen3.5:9b",
            "model": "qwen3.5:9b",
            "size": 6594474711,
            "details": {"family": "qwen35", "quantization_level": "Q4_K_M"},
            "capabilities": ["vision", "completion", "tools", "thinking"],
        },
        {
            "name": "qwen3:8b",
            "model": "qwen3:8b",
            "size": 5225388164,
            "capabilities": ["completion", "tools", "thinking"],
        },
    ]
}


def listing(handler: Callable[[httpx.Request], httpx.Response]) -> list[InstalledModel]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return installed_models(LlmSettings(base_url="http://127.0.0.1:11434/"), client)


def test_every_model_is_listed_with_what_it_can_do() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=TAGS)

    models = listing(handler)

    assert seen == ["http://127.0.0.1:11434/api/tags"]
    assert [model.name for model in models] == ["qwen3.5:9b", "qwen3:8b"]
    assert models[1].capabilities == {"completion", "tools", "thinking"}


def test_an_ollama_that_doesnt_list_capabilities_says_nothing_of_them() -> None:
    body = {"models": [{"name": "llama3:latest"}, {"model": "no name"}, "junk", None]}
    models = listing(lambda _request: httpx.Response(200, json=body))
    assert models == [InstalledModel("llama3:latest", None)]


def test_each_model_has_the_size_of_its_files_in_whole_mib() -> None:
    models = listing(lambda _request: httpx.Response(200, json=TAGS))
    assert [model.size_mib for model in models] == [6288, 4983]  # 6,594,474,711 bytes, rounded down
    odd = [{"name": f"m{n}", "size": size} for n, size in enumerate(["6 GB", -1, True, 1.5e9])]
    sizes = listing(lambda _request: httpx.Response(200, json={"models": odd}))
    assert [model.size_mib for model in sizes] == [None, None, None, None]


def loaded(handler: Callable[[httpx.Request], httpx.Response]) -> list[str]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return loaded_models(LlmSettings(base_url="http://127.0.0.1:11434/"), client)


def test_the_models_in_memory_are_named_from_its_ps() -> None:
    seen: list[str] = []
    # Trimmed from what Ollama 0.33.3 answered with her model loaded (measured).
    ps = {
        "models": [
            {"name": "qwen3.5:9b", "model": "qwen3.5:9b", "size_vram": 5729167604},
            {"model": "no name"},
            "junk",
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=ps)

    assert loaded(handler) == ["qwen3.5:9b"]
    assert seen == ["http://127.0.0.1:11434/api/ps"]
    assert loaded(lambda _request: httpx.Response(200, json={"models": []})) == []


@pytest.mark.parametrize(
    "response",
    [httpx.Response(404, text="404 page not found"), httpx.Response(200, json={"models": {}})],
)
def test_a_ps_that_doesnt_answer_as_ollama_is_unreachable(response: httpx.Response) -> None:
    with pytest.raises(OllamaUnreachableError):
        loaded(lambda _request: response)


@pytest.mark.parametrize(
    ("base_url", "here"),
    [
        ("http://127.0.0.1:11434", True),
        ("http://127.0.0.2:11434/", True),  # all of 127/8 is this computer
        ("http://[::1]:11434", True),
        ("http://localhost:11434", True),
        ("http://LocalHost:11434", True),
        ("http://192.168.1.20:11434", False),
        ("http://ollama.lan:11434", False),
        ("http://localhost.example.com:11434", False),
    ],
)
def test_only_an_address_on_this_computer_is_this_computer(base_url: str, here: bool) -> None:
    assert on_this_computer(LlmSettings(base_url=base_url)) is here


def test_no_models_is_an_empty_list() -> None:
    assert listing(lambda _request: httpx.Response(200, json={"models": None})) == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, text="404 page not found"),  # a base_url ending in /v1
        httpx.Response(500, json={"models": []}),  # the status decides, not a body that parses
        httpx.Response(200, text="<html>another server</html>"),
        httpx.Response(200, json={"models": "all of them"}),
        httpx.Response(200, json={"models": {}}),
        httpx.Response(200, json={"models": 0}),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"status": "ok"}),
    ],
)
def test_what_doesnt_answer_as_ollama_is_unreachable(response: httpx.Response) -> None:
    with pytest.raises(OllamaUnreachableError):
        listing(lambda _request: response)


def test_nothing_answering_is_unreachable() -> None:
    def refuse(request: httpx.Request) -> Any:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(OllamaUnreachableError):
        listing(refuse)


@pytest.mark.parametrize(
    ("asked", "found"),
    [
        ("qwen3.5:9b", "qwen3.5:9b"),
        ("llama3", "llama3:latest"),  # measured: "qwen3.5" asks for "qwen3.5:latest"
        ("registry.local:5000/team/model", "registry.local:5000/team/model:latest"),
        ("Qwen3.5:9B", "qwen3.5:9b"),
        ("library/qwen3.5:9b", "qwen3.5:9b"),
        ("registry.ollama.ai/library/qwen3.5:9b", "qwen3.5:9b"),
        ("qwen3.5", None),
        ("llama3:8b", None),
        ("someone/qwen3.5:9b", None),
    ],
)
def test_a_request_gets_the_model_its_name_resolves_to(asked: str, found: str | None) -> None:
    models = [
        InstalledModel(name, None)
        for name in ("qwen3.5:9b", "llama3:latest", "registry.local:5000/team/model:latest")
    ]
    match = find(models, asked)
    assert (match.name if match else None) == found
    assert all(same_model(model.name, asked) == (model.name == found) for model in models)
