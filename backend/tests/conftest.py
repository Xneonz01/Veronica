"""Shared test setup.

Several tests boot the real app through its lifespan, which opens the memory
database, the voice registry, the latency log, a black box and her data folder (the
personality, the policy, attachments, and the permissions it is given). Every one of those is
pointed at a throwaway folder for the whole session: without this, a test run would
write into the user's real `data/` (it once stamped a schema version into the real
memory.db) and telemetry retention would prune the real run history. Set outright, not
by default: a variable exported in the developer's shell would otherwise point the
suite at real data. The repository's .env isn't read at all.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app import left_behind
from app.core import config
from app.core.config import STORES

_SCRATCH = Path(tempfile.mkdtemp(prefix="veronica-test-data-"))

# The repository's .env is the developer's own: a test reads only the settings it sets.
# The tests of .env itself hand their own file in (`_env_file`).
config.Settings.model_config["env_file"] = None
config._ENV_FILE = _SCRATCH / "absent.env"  # where the profile step reads it
# Nor does the developer's own data decide what a start finds left behind.
left_behind.EARLIER = _SCRATCH / "installation-data"

# One folder, and every store in it, each set outright, so a variable exported in the
# developer's shell can't point a store at real data. The places are the settings' own
# (`STORES`).
_DATA = _SCRATCH / "data"
os.environ["VERONICA_DATA_DIR"] = str(_DATA)
for _section, _field, _place in STORES:
    os.environ[f"VERONICA_{_section.upper()}__{_field.upper()}"] = str(_DATA / _place)
# A start that loaded the real memory model would put it on the GPU for every test that
# boots the app; the ones about the warm-up turn it back on.
os.environ["VERONICA_MEMORY__WARM_ON_START"] = "false"
os.environ["VERONICA_LLM__WARM_ON_START"] = "false"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # The folder belongs to this session alone; without this, every run leaves one behind.
    shutil.rmtree(_SCRATCH, ignore_errors=True)


@pytest.fixture
def as_set_up(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Settings as a person's installation has them: this file names the data folder
    and every store in it, and here none is named."""
    monkeypatch.delenv("VERONICA_DATA_DIR", raising=False)
    for section, field, _place in STORES:
        monkeypatch.delenv(f"VERONICA_{section.upper()}__{field.upper()}", raising=False)
    return monkeypatch


@pytest.fixture(autouse=True)
def _models_on_disk(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every model counts as on disk, so an app booted here reports the same problems on
    any machine, whatever its model cache holds. The tests of the check itself carry the
    `model_files` mark and see the real one."""
    if request.node.get_closest_marker("model_files") is None:
        from app.adapters import model_files

        monkeypatch.setattr(model_files, "missing", lambda _settings: [])


@pytest.fixture(autouse=True)
def _card_unjudged(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A call's room on the graphics card isn't judged, so a call started here doesn't
    read this machine's card and Ollama, and starts on any machine whatever they hold. The
    tests of the check itself carry the `card_room` mark and see the real one."""
    if request.node.get_closest_marker("card_room") is None:
        from app.services.voice.realtime import card_room

        async def unjudged(_settings: object) -> None:
            return None

        monkeypatch.setattr(card_room, "measure", unjudged)


@pytest.fixture(autouse=True)
async def _close_what_a_failed_test_left_open() -> AsyncIterator[None]:
    """Close any memory store a test didn't: one that failed before its own close.

    Closing twice is harmless, so passing tests are unaffected. Without this, a failed
    assertion hung the whole run instead of reporting it (see `fakes.OPEN_STORES`).
    """
    yield
    from tests import fakes

    while fakes.OPEN_STORES:
        await fakes.OPEN_STORES.pop().close()
