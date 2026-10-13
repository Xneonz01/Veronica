"""Memory and document REST routes.

Covers browse/edit/pin/delete, approve/reject, learning mode, documents, undo and
quick capture. Mounts only the routers on a bare app with the fake memory runtime,
driven by httpx's ASGITransport in the test's own event loop (so the fake aiosqlite
store shares it).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes_memory import router as memory_router
from app.api.routes_rag import router as rag_router
from app.api.uploads import UPLOADS_DIR
from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.memory.runtime import MemoryRuntime
from tests.fakes import build_fake_memory_runtime


def _app(runtime: MemoryRuntime) -> FastAPI:
    app = FastAPI()
    app.state.memory_runtime = runtime
    # An upload's copy for its parser goes in her data folder (`api/uploads.py`).
    app.state.settings = get_settings()
    app.include_router(memory_router)
    app.include_router(rag_router)
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_list_edit_pin_delete(tmp_path: Path) -> None:
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await runtime.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Sourdough baking project")
    )
    async with _client(_app(runtime)) as client:
        listing = await client.get("/memory")
        assert listing.status_code == 200
        items = listing.json()["memories"]
        assert any("Sourdough" in m["content"] for m in items)
        memory_id = items[0]["id"]

        pinned = await client.patch(f"/memory/{memory_id}", json={"pinned": True})
        assert pinned.status_code == 200 and pinned.json()["pinned"] is True

        edited = await client.patch(
            f"/memory/{memory_id}", json={"content": "Sourdough + rye starter"}
        )
        assert edited.json()["content"] == "Sourdough + rye starter"

        removed = await client.delete(f"/memory/{memory_id}")
        assert removed.json() == {"deleted": memory_id}
        assert not (await runtime.service.list())  # archived -> gone from the active list
    await runtime.store.close()


async def test_approve_and_reject_candidates(tmp_path: Path) -> None:
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    keep = await runtime.service.add_candidate(
        MemoryCreate(kind=MemoryKind.fact, content="Has a cat named Max", source="inferred")
    )
    drop = await runtime.service.add_candidate(
        MemoryCreate(kind=MemoryKind.fact, content="Maybe likes jazz", source="inferred")
    )
    async with _client(_app(runtime)) as client:
        pending = (await client.get("/memory", params={"pending": "true"})).json()["memories"]
        assert {m["id"] for m in pending} == {keep.id, drop.id}

        approved = await client.post(f"/memory/{keep.id}/approve")
        assert approved.status_code == 200 and approved.json()["pending"] is False

        rejected = await client.post(f"/memory/{drop.id}/reject")
        assert rejected.json() == {"rejected": drop.id}

        active = (await client.get("/memory", params={"pending": "false"})).json()["memories"]
        ids = {m["id"] for m in active}
        assert keep.id in ids and drop.id not in ids  # approved kept, rejected archived
    await runtime.store.close()


async def test_reject_turns_down_only_a_candidate(tmp_path: Path) -> None:
    """Bug 11: reject archived any memory given it (mutation: archive whatever it is)."""
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    kept = await runtime.service.add(MemoryCreate(kind=MemoryKind.fact, content="Has a cat"))
    waiting = await runtime.service.add_candidate(
        MemoryCreate(kind=MemoryKind.fact, content="Maybe likes jazz", source="inferred")
    )
    async with _client(_app(runtime)) as client:
        assert (await client.post(f"/memory/{kept.id}/reject")).status_code == 404
        assert (await client.post(f"/memory/{waiting.id}/reject")).status_code == 200
        assert (await client.post(f"/memory/{waiting.id}/reject")).status_code == 404  # once
        assert (await client.post("/memory/987654/reject")).status_code == 404
    still = await runtime.service.get(kept.id)
    gone = await runtime.service.get(waiting.id)
    assert still is not None and not still.archived
    assert gone is not None and gone.archived
    await runtime.store.close()


async def test_a_candidate_turned_down_while_approved_stays_turned_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approving embeds first: a reject landing then left an archived memory with a live
    vector, announced as approved (mutation: activate whatever the approval found)."""
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    waiting = await runtime.service.add_candidate(
        MemoryCreate(kind=MemoryKind.fact, content="Maybe likes jazz", source="inferred")
    )
    embed = runtime.embedder.embed

    async def turned_down_meanwhile(texts: list[str]) -> list[list[float]]:
        await runtime.service.reject(waiting.id)
        return await embed(texts)

    monkeypatch.setattr(runtime.embedder, "embed", turned_down_meanwhile)
    after = await runtime.service.approve(waiting.id)

    assert after is not None and after.archived and after.pending
    async with runtime.store.db.execute(
        "SELECT COUNT(*) AS n FROM memory_vec WHERE memory_id=?", (waiting.id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row["n"] == 0
    await runtime.store.close()


async def test_the_page_lists_every_memory_she_keeps_not_the_200_newest(tmp_path: Path) -> None:
    """Bug 11: past 200 newer memories, an older profile slot and a pinned memory were off
    the Memory page, and out of its search (mutation: the 200 newest of all again)."""
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await runtime.service.set_profile("name", "Rowan")
    pinned = await runtime.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Allergic to walnuts", pinned=True)
    )
    for n in range(201):  # one over the cap, which keeps 200
        await runtime.service.add(MemoryCreate(kind=MemoryKind.fact, content=f"Note {n}"))
    await runtime.service.add_candidate(
        MemoryCreate(kind=MemoryKind.fact, content="Maybe likes jazz", source="inferred")
    )
    async with _client(_app(runtime)) as client:
        listed = (await client.get("/memory")).json()["memories"]
        found = (await client.get("/memory", params={"q": "WALNUTS"})).json()["memories"]
    assert {"Rowan", "Allergic to walnuts", "Maybe likes jazz"} <= {m["content"] for m in listed}
    assert len(listed) == 1 + 1 + 200 + 1
    assert [m["id"] for m in found] == [pinned.id]
    await runtime.store.close()


async def test_the_review_queue_shows_the_newest_candidates(tmp_path: Path) -> None:
    """(mutation: every candidate, however many learning has left)"""
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    for n in range(201):
        await runtime.service.add_candidate(
            MemoryCreate(kind=MemoryKind.fact, content=f"Candidate {n}", source="inferred")
        )
    async with _client(_app(runtime)) as client:
        waiting = (await client.get("/memory", params={"pending": "true"})).json()["memories"]
    assert len(waiting) == 200 and "Candidate 0" not in {m["content"] for m in waiting}
    await runtime.store.close()


async def test_learning_mode_get_and_put(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    broadcast: list[Event] = []

    async def capture(event: Event) -> None:
        broadcast.append(event)

    bus.subscribe("memory.learning_mode", capture)
    runtime = await build_fake_memory_runtime(tmp_path, bus)
    async with _client(_app(runtime)) as client:
        assert (await client.get("/memory/learning-mode")).json() == {"mode": "proactive"}

        put = await client.put("/memory/learning-mode", json={"mode": "review_only"})
        assert put.json() == {"mode": "review_only"}
        assert runtime.learning.mode.value == "review_only"  # hot-applied
        assert broadcast and broadcast[-1].payload == {"mode": "review_only"}  # live-reactive
    await runtime.store.close()


async def test_edit_rejects_sensitive_content(tmp_path: Path) -> None:
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    memory = await runtime.service.add(MemoryCreate(kind=MemoryKind.fact, content="router notes"))
    async with _client(_app(runtime)) as client:
        blocked = await client.patch(
            f"/memory/{memory.id}", json={"content": "my password is hunter2"}
        )
        assert blocked.status_code == 422
    await runtime.store.close()


async def test_an_uploaded_document_keeps_no_trace_of_its_temp_file(tmp_path: Path) -> None:
    # Uploads pass through a temp file whose path sits under the user's profile on
    # Windows. It used to be stored and served as the document's source_path.
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    async with _client(_app(runtime)) as client:
        added = await client.post(
            "/rag/documents",
            files={"file": ("notes.txt", b"Feed the starter carefully.", "text/plain")},
        )
        assert added.status_code == 200
        listed = (await client.get("/rag/documents")).json()["documents"]
    await runtime.store.close()
    temp_dir = tempfile.gettempdir().lower()
    for document in (added.json(), *listed):
        assert document["source_path"] is None
        assert not any(isinstance(v, str) and temp_dir in v.lower() for v in document.values())
    assert not list((get_settings().data_path() / UPLOADS_DIR).iterdir())


async def test_documents_add_text_list_delete(tmp_path: Path) -> None:
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    async with _client(_app(runtime)) as client:
        added = await client.post(
            "/rag/documents/text",
            json={
                "title": "Sourdough Guide",
                "text": "Feed the starter for the sourdough carefully.",
            },
        )
        assert added.status_code == 200
        document_id = added.json()["id"]

        docs = (await client.get("/rag/documents")).json()["documents"]
        assert any(d["title"] == "Sourdough Guide" for d in docs)

        removed = await client.delete(f"/rag/documents/{document_id}")
        assert removed.json() == {"deleted": document_id}
        assert not (await client.get("/rag/documents")).json()["documents"]
    await runtime.store.close()


async def test_add_emits_enriched_notification(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    events: list[Event] = []

    async def capture(event: Event) -> None:
        events.append(event)

    bus.subscribe("memory.updated", capture)
    runtime = await build_fake_memory_runtime(tmp_path, bus)
    await runtime.service.add(
        MemoryCreate(kind=MemoryKind.preference, content="The user likes tea", source="user")
    )
    payload = events[-1].payload
    assert payload["action"] == "added"
    assert payload["kind"] == "preference" and "scope" not in payload
    assert payload["source"] == "user" and payload["undoable"] is True
    assert payload["origin"] == "agent" and "tea" in payload["summary"] and "ts" in payload
    await runtime.store.close()


async def test_delete_notification_carries_kind(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    events: list[Event] = []

    async def capture(event: Event) -> None:
        events.append(event)

    bus.subscribe("memory.updated", capture)
    runtime = await build_fake_memory_runtime(tmp_path, bus)
    memory = await runtime.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Sourdough starter")
    )
    await runtime.service.delete(memory.id, origin="ui")
    payload = events[-1].payload
    assert payload["action"] == "removed" and payload["kind"] == "fact"  # removal keeps kind
    assert payload["origin"] == "ui" and payload["undoable"] is False
    await runtime.store.close()


async def test_ui_edit_is_not_undoable(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    events: list[Event] = []

    async def capture(event: Event) -> None:
        events.append(event)

    bus.subscribe("memory.updated", capture)
    runtime = await build_fake_memory_runtime(tmp_path, bus)
    memory = await runtime.service.add(MemoryCreate(kind=MemoryKind.fact, content="note"))
    async with _client(_app(runtime)) as client:
        await client.patch(f"/memory/{memory.id}", json={"pinned": True})
    edit = [e for e in events if e.payload.get("action") == "updated"][-1]
    assert edit.payload["origin"] == "ui" and edit.payload["undoable"] is False
    await runtime.store.close()


async def test_undo_add_archives_it(tmp_path: Path) -> None:
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    memory = await runtime.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="sourdough starter", source="user")
    )
    async with _client(_app(runtime)) as client:
        res = await client.post(f"/memory/{memory.id}/undo")
        assert res.status_code == 200 and res.json() == {"undone": memory.id, "restored": None}
    assert not await runtime.service.list()  # archived
    await runtime.store.close()


async def test_undo_supersede_restores_original(tmp_path: Path) -> None:
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    old = await runtime.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="sourdough baking notes", source="user")
    )
    new = await runtime.service.supersede(
        old.id,
        MemoryCreate(kind=MemoryKind.fact, content="sourdough starter notes", source="inferred"),
    )
    assert [m.content for m in await runtime.service.list()] == ["sourdough starter notes"]
    async with _client(_app(runtime)) as client:
        res = await client.post(f"/memory/{new.id}/undo")
        assert res.json()["restored"] == old.id
    assert [m.content for m in await runtime.service.list()] == ["sourdough baking notes"]
    ctx = await runtime.retriever.retrieve("sourdough")
    assert any("baking" in item.memory.content for item in ctx.items)  # re-embedded -> retrievable
    await runtime.store.close()


# -- quick capture -------------------------------------------------------------


async def test_quick_capture_saves_a_note_through_the_one_write_path(tmp_path: Path) -> None:
    """`POST /memory` saves a note from the companion window without the model.

    It goes through `add_if_new`, like `memory_save`, so the same dedup, event and
    on-screen receipt apply.
    """
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    async with _client(_app(runtime)) as client:
        response = await client.post(
            "/memory", json={"content": "The boiler service is due in March"}
        )

    assert response.status_code == 201
    body = response.json()
    assert body["content"] == "The boiler service is due in March"
    assert body["source"] == "user"  # typed deliberately: explicit, not inferred
    assert [m.content for m in await runtime.service.list()] == [
        "The boiler service is due in March"
    ]
    await runtime.store.close()


async def test_quick_capture_announces_the_write(tmp_path: Path) -> None:
    """A quick-capture write publishes the event that drives the on-screen receipt."""
    bus = InProcessEventBus()
    seen: list[Event] = []

    async def capture(event: Event) -> None:
        seen.append(event)

    bus.subscribe("memory.updated", capture)
    runtime = await build_fake_memory_runtime(tmp_path, bus)
    async with _client(_app(runtime)) as client:
        await client.post("/memory", json={"content": "Bin day is Tuesday"})

    assert seen, "the write was silent"
    assert seen[0].payload["action"] == "added"
    await runtime.store.close()


async def test_quick_capture_refuses_a_secret(tmp_path: Path) -> None:
    """The sensitivity guard applies to this write path too."""
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    async with _client(_app(runtime)) as client:
        response = await client.post("/memory", json={"content": "the safe code is 4455"})

    assert response.status_code == 422
    assert await runtime.service.list() == []
    await runtime.store.close()


async def test_quick_capture_says_so_rather_than_writing_a_duplicate(tmp_path: Path) -> None:
    """A duplicate note returns 409 instead of claiming a write that didn't happen."""
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    async with _client(_app(runtime)) as client:
        first = await client.post("/memory", json={"content": "Bin day is Tuesday"})
        second = await client.post("/memory", json={"content": "Bin day is Tuesday"})

    assert first.status_code == 201
    assert second.status_code == 409
    assert len(await runtime.service.list()) == 1
    await runtime.store.close()


async def test_quick_capture_rejects_an_empty_note(tmp_path: Path) -> None:
    """Blank and whitespace-only notes are rejected with 422.

    The length check must run on the stripped string; otherwise "   " passes
    `min_length` and an empty memory is stored.
    """
    runtime = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    async with _client(_app(runtime)) as client:
        assert (await client.post("/memory", json={"content": "   "})).status_code == 422
        assert (await client.post("/memory", json={"content": ""})).status_code == 422

    assert await runtime.service.list() == []
    await runtime.store.close()
