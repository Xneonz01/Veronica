"""The voice registry's files: saved by name in its folder, so the folder can move (bug 27).

Moving the data folder moves the voices folder with it (0.29.0), and each voice's clip
was saved by its full path, so every call failed as it started.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.voice.voices import Voice, VoiceRegistry


def _saved(folder: Path) -> dict[str, dict[str, str | None]]:
    entries = json.loads((folder / "voices.json").read_text(encoding="utf-8"))
    return {entry["voice_id"]: entry for entry in entries}


def _clip(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF")
    return path


def test_a_voices_files_in_its_folder_are_saved_by_name(tmp_path: Path) -> None:
    """In memory each path is a full one (mutations: save full paths; leave the
    conditioning's path out; keep a name as given)."""
    folder = tmp_path / "voices"
    registry = VoiceRegistry(folder)
    registry.add(
        Voice(
            voice_id="rowan",
            name="Rowan",
            reference_wav_path=str(_clip(folder / "rowan.wav")),
            embedding_path=str(registry.embedding_path_for("rowan")),
        )
    )
    registry.add(Voice(voice_id="morgan", name="Morgan", reference_wav_path="morgan.wav"))

    saved = _saved(folder)
    assert saved["rowan"]["reference_wav_path"] == "rowan.wav"
    assert saved["rowan"]["embedding_path"] == "rowan.conds.pt"
    assert saved["morgan"]["embedding_path"] is None
    assert registry.get("rowan").reference_wav_path == str(folder / "rowan.wav")
    assert registry.get("morgan").reference_wav_path == str(folder / "morgan.wav")


def test_a_moved_folder_keeps_its_voices(tmp_path: Path) -> None:
    """(mutation: read a name as a path from wherever the backend runs)"""
    before = tmp_path / "before" / "voices"
    registry = VoiceRegistry(before)
    registry.add(
        Voice(voice_id="rowan", name="Rowan", reference_wav_path=str(_clip(before / "rowan.wav")))
    )
    after = tmp_path / "after" / "voices"
    after.parent.mkdir()
    before.rename(after)

    assert VoiceRegistry(after).get("rowan").reference_wav_path == str(after / "rowan.wav")


def test_a_full_path_saved_before_the_move_is_found_by_its_name(tmp_path: Path) -> None:
    """What versions before 0.29.0 saved (mutation: keep the stale path)."""
    folder = tmp_path / "voices"
    clip = _clip(folder / "rowan.wav")
    gone = tmp_path / "old-install" / "data" / "voices" / "rowan.wav"
    entry = {"voice_id": "rowan", "name": "Rowan", "reference_wav_path": str(gone)}
    (folder / "voices.json").write_text(json.dumps([entry]), encoding="utf-8")

    assert VoiceRegistry(folder).get("rowan").reference_wav_path == str(clip)


def test_a_clip_kept_elsewhere_stays_where_it_is(tmp_path: Path) -> None:
    """Found at its full path, it is used there, even with a file of its name in the
    folder, and saved as it was given (mutations: look in the folder first; save every
    file by name)."""
    folder = tmp_path / "voices"
    _clip(folder / "rowan.wav")
    elsewhere = _clip(tmp_path / "recordings" / "rowan.wav")
    VoiceRegistry(folder).add(
        Voice(voice_id="rowan", name="Rowan", reference_wav_path=str(elsewhere))
    )

    assert _saved(folder)["rowan"]["reference_wav_path"] == str(elsewhere)
    assert VoiceRegistry(folder).get("rowan").reference_wav_path == str(elsewhere)


def test_a_clip_that_is_nowhere_keeps_the_path_it_had(tmp_path: Path) -> None:
    """A call then fails as it starts, as it did: nothing is made up for it."""
    folder = tmp_path / "voices"
    gone = tmp_path / "old-install" / "rowan.wav"
    VoiceRegistry(folder).add(Voice(voice_id="rowan", name="Rowan", reference_wav_path=str(gone)))

    assert VoiceRegistry(folder).get("rowan").reference_wav_path == str(gone)


def test_a_clip_in_the_folder_named_another_way_is_saved_by_name(tmp_path: Path) -> None:
    """(mutation: compare the folder as spelled)"""
    folder = tmp_path / "voices"
    registry = VoiceRegistry(folder)
    clip = _clip(folder / "rowan.wav")
    spelled = tmp_path / "voices" / ".." / "voices" / clip.name
    registry.add(Voice(voice_id="rowan", name="Rowan", reference_wav_path=str(spelled)))

    assert _saved(folder)["rowan"]["reference_wav_path"] == "rowan.wav"


def test_a_folder_closed_to_her_leaves_the_path_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read at start-up: a clip she can't look at must not stop it (mutation: let the
    error through)."""
    folder = tmp_path / "voices"
    stored = tmp_path / "closed" / "rowan.wav"
    entry = {"voice_id": "rowan", "name": "Rowan", "reference_wav_path": str(stored)}
    folder.mkdir()
    (folder / "voices.json").write_text(json.dumps([entry]), encoding="utf-8")

    def closed(self: Path, *, follow_symlinks: bool = True) -> bool:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "exists", closed)

    assert VoiceRegistry(folder).get("rowan").reference_wav_path == str(stored)
