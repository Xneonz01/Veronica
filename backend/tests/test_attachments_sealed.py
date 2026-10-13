"""What the user attaches is sealed at rest, and an upload's copy never leaves her data
folder.

Images attached in chat were written as they came, and a document's copy for the parser
went to the temp folder every program on the account shares, and a crash left it
behind.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.uploads import UPLOADS_DIR, private_copy, sweep_uploads
from app.core.event_bus import InProcessEventBus
from app.core.sealed import MAGIC, SealBroken, unseal
from app.services.chat.attachments import AttachmentStore
from tests.fakes import build_fake_memory_runtime

KEY = os.urandom(32)
PHOTO = b"\x89PNG\r\n\x1a\n a receipt with a card number on it"


async def _store(tmp_path: Path, key: bytes | None = KEY) -> tuple[AttachmentStore, Path]:
    memory = await build_fake_memory_runtime(tmp_path / "db", InProcessEventBus())
    root = tmp_path / "data" / "attachments"
    store = AttachmentStore(memory.store, root, key)
    await store.init_schema()
    return store, root


async def test_an_attached_image_is_sealed_with_the_data_key(tmp_path: Path) -> None:
    """(mutation: write it as it came)"""
    store, root = await _store(tmp_path)

    added = await store.add(1, kind="image", name="Receipt.PNG", size_bytes=len(PHOTO), data=PHOTO)

    stored = root / f"{added.id}.png"
    sealed = stored.read_bytes()
    assert sealed.startswith(MAGIC) and PHOTO not in sealed
    assert unseal(sealed, KEY, stored.name) == PHOTO


async def test_a_sealed_image_cant_stand_in_for_another(tmp_path: Path) -> None:
    """Bound to its own file name: one attachment's file renamed to another's fails."""
    store, root = await _store(tmp_path)
    first = await store.add(1, kind="image", name="a.png", size_bytes=1, data=PHOTO)

    with pytest.raises(SealBroken):
        unseal((root / f"{first.id}.png").read_bytes(), KEY, f"{first.id + 1}.png")


async def test_without_a_data_key_an_image_is_kept_as_it_came(tmp_path: Path) -> None:
    """Not Windows: nothing to seal with, as for her voice clips."""
    store, root = await _store(tmp_path, key=None)

    added = await store.add(1, kind="image", name="a.png", size_bytes=1, data=PHOTO)

    assert (root / f"{added.id}.png").read_bytes() == PHOTO


async def test_one_left_plain_by_an_earlier_version_is_sealed_at_start(tmp_path: Path) -> None:
    """(mutation: skip sealing what is already there)"""
    root = tmp_path / "data" / "attachments"
    root.mkdir(parents=True)
    (root / "7.png").write_bytes(PHOTO)

    await _store(tmp_path)

    assert unseal((root / "7.png").read_bytes(), KEY, "7.png") == PHOTO
    assert not list(root.glob("*.sealing"))


def _request(data_dir: Path) -> object:
    settings = SimpleNamespace(data_path=lambda: data_dir)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


def test_an_uploads_copy_is_made_in_her_data_folder_and_gone_after(tmp_path: Path) -> None:
    """(mutations: use the temp folder; keep the copy)"""
    with private_copy(_request(tmp_path), b"notes", "Plan.MD") as copy:  # type: ignore[arg-type]
        assert copy.parent == tmp_path / UPLOADS_DIR
        assert copy.suffix == ".md" and copy.read_bytes() == b"notes"

    assert not copy.exists()


@pytest.mark.parametrize("name", ["evil.p d f", "x.\u202epdf", "no-suffix", "a." + "x" * 20])
def test_a_suffix_that_isnt_one_becomes_bin(name: str, tmp_path: Path) -> None:
    with private_copy(_request(tmp_path), b"x", name) as copy:  # type: ignore[arg-type]
        assert copy.suffix == ".bin"


def test_copies_a_crash_left_behind_are_swept_at_start(tmp_path: Path) -> None:
    folder = tmp_path / UPLOADS_DIR
    folder.mkdir()
    (folder / "tmp1234.pdf").write_bytes(b"a document's text")

    sweep_uploads(tmp_path)

    assert list(folder.iterdir()) == []
