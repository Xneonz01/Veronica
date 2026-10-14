"""The application's name, version and release channel, defined once.

Nothing else hard-codes these. They must match the newest entry in the repository's
`CHANGELOG.yaml`, and the version must also match `backend/pyproject.toml`,
`frontend/package.json` and the Tauri manifests — `tests/test_version_drift.py`
fails if any of them disagree.

Versioning: while the project is pre-1.0, a release that adds a feature bumps the
minor number and a release that only fixes things bumps the patch number.
"""

APP_NAME = "Veronica"
APP_VERSION = "0.36.0"
APP_VERSION_LABEL = "Out loud"
APP_CHANNEL = "dev"  # dev | beta | stable
