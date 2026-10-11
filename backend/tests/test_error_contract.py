"""The typed error contract, and stream failures that must not pass silently.

The frontend maps an error `code` to user-facing copy, so every failure needs a
stable, machine-readable shape. FastAPI's default 422 puts a list of validator
errors in `detail`, and its default 500 is untyped `text/plain`; neither can be
mapped, so both are replaced.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters.llm.ollama_errors import OllamaStreamError
from app.main import create_app


def test_validation_error_is_typed_not_a_pydantic_array() -> None:
    with TestClient(create_app(), base_url="http://localhost") as client:
        res = client.put("/memory/learning-mode", json={"mode": "bogus"})
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "invalid_input"
    # A list here would reach React as a child object and crash the render.
    assert isinstance(body["detail"], str)


def test_unhandled_error_returns_a_mappable_code(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()

    @app.get("/_boom")
    async def boom() -> None:
        raise RuntimeError("CUDA out of memory")

    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
        res = client.get("/_boom")
    assert res.status_code == 500
    body = res.json()
    assert body["code"] == "internal"
    assert "CUDA" not in body["detail"]  # the raw exception never reaches the client


async def test_ollama_midstream_error_raises_instead_of_faking_success() -> None:
    # Ollama reports a mid-generation failure as HTTP 200 with a bare {"error": ...}
    # line. Ending the stream quietly would leave `done` unsent and the chunker
    # unflushed, and record the truncated reply as a successful turn.
    import httpx

    from app.adapters.llm.ollama_client import OllamaLLMClient
    from app.core.config import get_settings

    lines = [
        json.dumps({"message": {"content": "Sure, the answer is"}}),
        json.dumps({"error": "model runner has unexpectedly stopped"}),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="\n".join(lines))

    transport = httpx.MockTransport(handler)
    client = OllamaLLMClient(
        get_settings().llm, httpx.AsyncClient(transport=transport, base_url="http://x")
    )
    with pytest.raises(OllamaStreamError):
        async for _ in client.stream_chat([{"role": "user", "content": "hi"}]):
            pass
    await client.aclose()


async def test_truncated_stream_raises() -> None:
    # Same failure class: the body ends without a done marker.
    import httpx

    from app.adapters.llm.ollama_client import OllamaLLMClient
    from app.core.config import get_settings

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"message": {"content": "half a rep"}}))

    client = OllamaLLMClient(
        get_settings().llm,
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x"),
    )
    with pytest.raises(OllamaStreamError):
        async for _ in client.stream_chat([{"role": "user", "content": "hi"}]):
            pass
    await client.aclose()
