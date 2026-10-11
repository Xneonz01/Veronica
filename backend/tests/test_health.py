"""Health endpoint smoke test (also exercises the app lifespan)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.version import APP_CHANNEL, APP_VERSION, APP_VERSION_LABEL
from app.main import create_app


def test_health_ok() -> None:
    """GET /health boots the app and returns 200 with the expected keys."""
    with TestClient(create_app(), base_url="http://localhost") as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "status",
        "app",
        "version",
        "label",
        "channel",
        "env",
        "profile",
        "problems",
    }
    assert (body["status"], body["problems"]) == ("ok", [])
    assert body["app"] == "Veronica"


def test_health_reports_the_running_version() -> None:
    with TestClient(create_app(), base_url="http://localhost") as client:
        body = client.get("/health").json()
    assert body["version"] == APP_VERSION
    assert body["label"] == APP_VERSION_LABEL  # read from CHANGELOG.yaml at startup
    assert body["channel"] == APP_CHANNEL
