"""Uploads are read up to a cap and refused past it, in plain words (bug 12).

Chat attachments were capped; documents and voice clips were read whole, however big.
The cap is made small here rather than sending 25 MB.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api import uploads
from app.api.routes_rag import router as rag_router
from app.api.routes_voices import MAX_CLIPS
from app.core.event_bus import InProcessEventBus
from app.main import create_app
from tests.fakes import build_fake_memory_runtime
from tests.test_voice_studio import _runtime_with_studio, _tone_bytes


def _upload(data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename="notes.txt")


async def test_a_file_within_the_cap_is_read_whole_and_one_past_it_is_not() -> None:
    assert await uploads.read_capped(_upload(b"x" * 100), 100) == b"x" * 100
    assert await uploads.read_capped(_upload(b"x" * 101), 100) is None


async def test_a_file_past_the_cap_is_refused_in_plain_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 10)

    with pytest.raises(HTTPException) as refused:
        await uploads.read_or_refuse(_upload(b"x" * 11))

    assert refused.value.status_code == 422 and refused.value.detail == uploads.TOO_LARGE


async def test_a_document_past_the_cap_is_refused_and_nothing_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: read the document whole)"""
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 16)
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    app = FastAPI()
    app.include_router(rag_router)
    app.state.memory_runtime = runtime

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as client:
        added = await client.post(
            "/rag/documents", files={"file": ("notes.txt", b"x" * 64, "text/plain")}
        )
        listed = (await client.get("/rag/documents")).json()["documents"]

    assert added.status_code == 422 and added.json()["detail"] == uploads.TOO_LARGE
    assert listed == []
    await runtime.store.close()


def test_a_voice_clip_past_the_cap_or_too_many_clips_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: read every clip whole, or take any number of them)"""
    app = create_app()
    clip = _tone_bytes(seconds=7.0)
    with TestClient(app, base_url="http://localhost") as client:
        app.state.voice_runtime = _runtime_with_studio(tmp_path, app.state.event_bus)
        form = {"name": "Studio Voice", "consent": "true", "source": "upload"}

        many = [("files", (f"clip{n}.wav", clip, "audio/wav")) for n in range(MAX_CLIPS + 1)]
        too_many = client.post("/voices", data=form, files=many)

        monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", len(clip) - 1)
        one = {"files": ("clip.wav", clip, "audio/wav")}
        too_big = client.post("/voices", data=form, files=one)
        voices = client.get("/voices").json()["voices"]

    assert too_many.status_code == 422 and str(MAX_CLIPS) in too_many.json()["detail"]
    assert too_big.status_code == 422 and too_big.json()["detail"] == uploads.TOO_LARGE
    assert [voice["name"] for voice in voices if voice["name"] == "Studio Voice"] == []
