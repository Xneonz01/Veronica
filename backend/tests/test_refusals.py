"""A route's refusal reaches the app with its code and plain words, `{"code", "detail"}`:
the personality route and the upload refusals sent the words alone, so the app could
show them but never tell one refusal from another (`app/api/refusals.py`)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_rag, uploads
from app.api.refusals import Refusal
from app.main import create_app
from app.services.persona.defaults import MAX_PERSONALITY_CHARS


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), base_url="http://localhost") as started:
        yield started


def _body(response: Any) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    return body


def test_a_personality_too_long_says_which_refusal(client: TestClient) -> None:
    response = client.put("/persona", json={"personality": "x" * (MAX_PERSONALITY_CHARS + 1)})

    assert response.status_code == 422
    assert _body(response)["code"] == "persona_too_long"
    assert isinstance(_body(response)["detail"], str)


def test_a_personality_that_fills_the_window_says_which_refusal(client: TestClient) -> None:
    """Held to chat's window from the start: 1,200 emoji are within the length and
    about 4,800 tokens (mutation: never set the check up)."""
    before = client.get("/persona").json()["personality"]

    response = client.put("/persona", json={"personality": chr(0x1F600) * 1200})

    assert response.status_code == 422
    assert _body(response)["code"] == "persona_fills_window"
    assert client.get("/persona").json()["personality"] == before
    longest = "Be dry. " * (MAX_PERSONALITY_CHARS // 8)  # English at the length: it fits
    assert client.put("/persona", json={"personality": longest}).status_code == 200
    client.post("/persona/reset")


@pytest.mark.parametrize(
    ("route", "kwargs", "code"),
    [
        (
            "/rag/documents",
            {"files": {"file": ("notes.xyz", b"hello", "text/plain")}},
            "unsupported_file",
        ),
        ("/rag/documents/text", {"json": {"title": "Blank", "text": "   "}}, "empty_document"),
        (
            "/voices",
            {
                "data": {"name": "Rowan", "consent": "false"},
                "files": {"files": ("a.wav", b"RIFF", "audio/wav")},
            },
            "consent_missing",
        ),
    ],
)
def test_an_upload_refused_says_which_refusal(
    client: TestClient, route: str, kwargs: dict[str, Any], code: str
) -> None:
    """(mutation: raise the bare HTTPException again)"""
    response = client.post(route, **kwargs)

    assert (response.status_code in (400, 422), _body(response)["code"]) == (True, code)
    assert isinstance(_body(response)["detail"], str)


@pytest.mark.parametrize(("method", "route"), [("post", "activate"), ("post", "preview")])
def test_a_voice_that_isnt_there_is_a_404_with_its_code(
    client: TestClient, method: str, route: str
) -> None:
    """Activating one answered with the words alone (mutation: the bare 404 again)."""
    response = getattr(client, method)(f"/voices/no-such-voice/{route}")

    assert (response.status_code, _body(response)["code"]) == (404, "voice_missing")


def test_another_missing_key_while_activating_a_voice_is_not_a_missing_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any KeyError was answered "that voice no longer exists", a fault in the voice
    model's own state among them (mutation: catch every KeyError again)."""
    app = create_app()

    async def broken(_voice_id: str) -> None:
        raise KeyError("conditioning")

    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as started:
        monkeypatch.setattr(app.state.voice_runtime.voice_service, "set_active", broken)
        response = started.post("/voices/no-such-voice/activate")

    assert (response.status_code, _body(response)["code"]) == (500, "internal")


def test_a_file_past_the_cap_says_so_with_its_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 8)

    response = client.post("/rag/documents", files={"file": ("notes.txt", b"x" * 64, "text/plain")})

    assert _body(response) == {"code": "file_too_large", "detail": uploads.TOO_LARGE}


def test_pasted_text_past_the_limit_is_refused_before_it_is_parsed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body is read only as far as the limit (mutation: parse it first, as a declared
    model would)."""
    monkeypatch.setattr(routes_rag, "_MAX_TEXT_BODY_BYTES", 64)
    parsed: list[bytes] = []
    validate = routes_rag.TextDocument.model_validate_json

    def watched(raw: bytes) -> routes_rag.TextDocument:
        parsed.append(raw)
        return validate(raw)

    monkeypatch.setattr(routes_rag.TextDocument, "model_validate_json", watched)

    response = client.post("/rag/documents/text", json={"title": "Notes", "text": "x" * 200})

    assert (response.status_code, _body(response)["code"]) == (422, "text_too_long")
    assert parsed == []


def test_pasted_text_without_a_title_is_invalid_input_and_stays_out_of_the_log(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    response = client.post("/rag/documents/text", json={"text": "my private notes"})

    assert (response.status_code, _body(response)["code"]) == (422, "invalid_input")
    assert _body(response)["field"] == "title"
    assert "my private notes" not in caplog.text


def test_no_invalid_request_puts_what_was_sent_in_the_log(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the paste route kept its body out: a memory over its length went to the log
    whole (mutation: log the errors as they come)."""
    caplog.set_level("INFO")
    note = "Rowan's private note " * 120

    response = client.post("/memory", json={"kind": "fact", "content": note})

    assert (response.status_code, _body(response)["code"]) == (422, "invalid_input")
    assert "private note" not in caplog.text


def test_without_the_handler_a_refusal_is_still_an_http_error() -> None:
    """A router mounted on its own (as the route tests do) answers with the status and
    the words, as before (mutation: a plain Exception)."""
    app = FastAPI()

    @app.get("/refused")
    async def refused() -> None:
        raise Refusal(422, "any_code", "Plain words.")

    response = TestClient(app).get("/refused")

    assert (response.status_code, response.json()) == (422, {"detail": "Plain words."})
