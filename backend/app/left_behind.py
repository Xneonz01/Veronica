"""Her memory and voices where versions before 0.29.0 kept them, the installation's own
data folder, while the settings now keep them elsewhere.

Before 0.29.0, moving `data_dir` moved only her personality, policy and attachments, so
someone who had moved it finds her memory and voices left behind. They are never moved
for her: that is the person's to do, with her stopped. Until then they are named in the
log at every start, and her memory is not opened at the new place at all: opening it
would start a second, empty one, and moving the old file over it later would lose
whatever was saved in between (`app/stores.py` starts without it and says why).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import Settings
from app.paths import INSTALL_ROOT, anchored

logger = logging.getLogger(__name__)

# Where they were kept. The tests point it elsewhere, so a developer's own data never
# decides what a test sees.
EARLIER = INSTALL_ROOT / "data"


class MemoryLeftBehind(Exception):
    """Her memory is still in the installation's data folder, and none is where the
    settings keep it now."""


def left_behind(settings: Settings) -> list[str]:
    """What is still in the installation's data folder while she keeps it elsewhere:
    "memory", "voices". Judged by the files, not the folders, so a folder emptied by
    moving what was in it counts as moved."""
    return [name for name, (now, then) in _places(settings).items() if _left(now, then)]


def warn(settings: Settings) -> list[str]:
    """`left_behind`, said in the log (no path: it goes to the black box too)."""
    left = left_behind(settings)
    if left:
        logger.warning(
            "the installation's data folder still holds her %s, which she no longer reads "
            "there: stop her backend and move each folder whole into her data folder, or "
            "delete them if they are copies",
            " and ".join(left),
        )
    return left


def memory_left_behind(settings: Settings) -> bool:
    """Whether opening her memory would start a second, empty one: none where the
    settings keep it, and hers still in the installation's data folder."""
    now, then = _places(settings)["memory"]
    return _left(now, then) and not _exists(now)


def _places(settings: Settings) -> dict[str, tuple[Path, Path]]:
    """Each store's file where the settings keep it now, and where it was kept."""
    voices = anchored(settings.voice.voices_dir) / "voices.json"
    return {
        "memory": (anchored(settings.memory.db_path), EARLIER / "memory" / "memory.db"),
        "voices": (voices, EARLIER / "voices" / "voices.json"),
    }


def _left(now: Path, then: Path) -> bool:
    try:
        return now.resolve() != then.resolve() and then.exists()
    except OSError:  # a folder closed to this account: nothing can be said about it
        return False


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return True  # can't be told apart from there: opening it will say what's wrong
