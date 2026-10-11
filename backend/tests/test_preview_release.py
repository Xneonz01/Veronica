"""A voice preview asks for the voice model to be let go afterwards, as the end of a call
does (bug 13: outside a call it stayed loaded until the next call ended)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_a_preview_asks_for_the_release_even_when_it_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview can fail after the model loaded, so the release is asked for either way
    (mutation: ask only after a preview that worked)."""
    app = create_app()
    asked: list[float] = []
    with TestClient(app, base_url="http://localhost") as client:
        monkeypatch.setattr(app.state.voice_runtime.calls, "release_later", asked.append)
        response = client.post("/voices/no-such-voice/preview")

    assert response.status_code == 404
    assert asked == [app.state.settings.realtime.release_models_after_s]
