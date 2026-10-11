"""Attachments and project scoping.

The key property is that a project's scope changes what retrieval can return.
sqlite-vec spends `k` before the JOIN, so a scope applied to the results instead of
to the search would quietly return fewer chunks than asked for.
"""

from __future__ import annotations

from pathlib import Path

from app.core.event_bus import InProcessEventBus
from app.services.chat.attachments import AttachmentStore, classify
from app.services.chat.projects import ProjectStore
from tests.fakes import build_fake_chat_store, build_fake_memory_runtime


async def _stores(tmp_path: Path):
    mem = await build_fake_memory_runtime(tmp_path / "db", InProcessEventBus())
    chat = await build_fake_chat_store(mem.store)
    projects = ProjectStore(mem.store)
    await projects.init_schema()
    attachments = AttachmentStore(mem.store, tmp_path / "data" / "attachments")
    await attachments.init_schema()
    return mem, chat, projects, attachments


# -- classification ------------------------------------------------------------


def test_files_are_classified_before_anything_is_read() -> None:
    assert classify("notes.md") == "document"
    assert classify("main.py") == "document"  # code reads like text once allowed
    assert classify("report.PDF") == "document"  # case is not a distinction
    assert classify("photo.jpg") == "image"
    assert classify("installer.exe") == "unsupported"
    assert classify("no-extension") == "unsupported"


# -- attachments ---------------------------------------------------------------


async def test_an_image_is_stored_and_honestly_marked_unread(tmp_path: Path) -> None:
    """An image is stored with `understood=False`, since no vision model reads it.

    Recording otherwise would let the UI imply the picture had been looked at.
    """
    mem, chat, _projects, attachments = await _stores(tmp_path)
    conversation = await chat.create("with a photo")

    saved = await attachments.add(
        conversation.id, kind="image", name="roof.png", size_bytes=3, data=b"png"
    )
    assert saved.understood is False
    assert attachments.path_for(saved.id, "roof.png").read_bytes() == b"png"

    rows = await attachments.list_for(conversation.id)
    assert [(r.kind, r.understood) for r in rows] == [("image", False)]
    await mem.store.close()


async def test_a_stored_file_is_named_by_id_not_by_the_users_filename(tmp_path: Path) -> None:
    # The filename is user input; using it in the stored path would allow traversal.
    mem, chat, _projects, attachments = await _stores(tmp_path)
    conversation = await chat.create("c")
    saved = await attachments.add(
        conversation.id, kind="image", name="../../escape.png", size_bytes=1, data=b"x"
    )
    stored = attachments.path_for(saved.id, "../../escape.png")
    assert stored.name == f"{saved.id}.png"
    assert stored.parent == tmp_path / "data" / "attachments"
    await mem.store.close()


async def test_removing_an_attachment_reports_the_document_to_unindex(tmp_path: Path) -> None:
    mem, chat, _projects, attachments = await _stores(tmp_path)
    conversation = await chat.create("c")
    saved = await attachments.add(
        conversation.id, kind="document", name="notes.md", size_bytes=10, document_id=7
    )

    removed, document_id = await attachments.remove(saved.id)
    assert removed is True and document_id == 7  # the caller unindexes it from RAG
    assert await attachments.list_for(conversation.id) == []
    assert await attachments.remove(saved.id) == (False, None)  # already gone, not an error
    await mem.store.close()


async def test_a_moved_data_folder_leaves_no_attachment_behind(tmp_path: Path) -> None:
    """After `data_dir` moves, a file is either where it was written or, moved with the
    folder, where it would be now; deleting removes it from either (mutation: delete
    only where it would be now)."""
    mem, chat, _projects, attachments = await _stores(tmp_path)
    conversation = await chat.create("c")
    for name in ("left.png", "moved.png", "single.png"):
        await attachments.add(conversation.id, kind="image", name=name, size_bytes=1, data=b"x")
    left, moved, single = await attachments.list_for(conversation.id)
    elsewhere = AttachmentStore(mem.store, tmp_path / "moved" / "attachments")
    (tmp_path / "moved" / "attachments").mkdir(parents=True)
    attachments.path_for(moved.id, moved.name).rename(elsewhere.path_for(moved.id, moved.name))

    await elsewhere.remove(single.id)
    await elsewhere.purge_for_conversation(conversation.id)

    assert list((tmp_path / "data" / "attachments").iterdir()) == []
    assert list((tmp_path / "moved" / "attachments").iterdir()) == []
    await mem.store.close()


async def test_a_stored_path_naming_another_file_is_not_deleted(tmp_path: Path) -> None:
    """The stored path comes from the database: trusted only when it names this
    attachment's own file (mutation: delete whatever it names)."""
    mem, chat, _projects, attachments = await _stores(tmp_path)
    conversation = await chat.create("c")
    saved = await attachments.add(
        conversation.id, kind="image", name="roof.png", size_bytes=1, data=b"x"
    )
    other = tmp_path / "keep.txt"
    other.write_text("not an attachment", encoding="utf-8")
    await mem.store.db.execute(
        "UPDATE chat_attachments SET stored_path = ? WHERE id = ?", (str(other), saved.id)
    )
    await mem.store.db.commit()

    await attachments.purge_for_conversation(conversation.id)

    assert other.exists()
    assert not attachments.path_for(saved.id, "roof.png").exists()
    await mem.store.close()


async def test_a_same_named_file_outside_an_attachments_folder_is_kept(tmp_path: Path) -> None:
    """A stale stored path can name another folder's "12.png" (mutation: trust the name
    alone)."""
    mem, chat, _projects, attachments = await _stores(tmp_path)
    conversation = await chat.create("c")
    saved = await attachments.add(
        conversation.id, kind="image", name="roof.png", size_bytes=1, data=b"x"
    )
    other = tmp_path / "pictures" / f"{saved.id}.png"
    other.parent.mkdir()
    other.write_bytes(b"someone else's")
    await mem.store.db.execute(
        "UPDATE chat_attachments SET stored_path = ? WHERE id = ?", (str(other), saved.id)
    )
    await mem.store.db.commit()

    await attachments.purge_for_conversation(conversation.id)

    assert other.exists()
    await mem.store.close()


async def test_a_file_held_open_leaves_the_rest_deleted_and_unindexed(tmp_path: Path) -> None:
    """Another program holding one open raised before the documents to unindex were
    returned, so they stayed retrievable (mutation: let the error through)."""
    mem, chat, _projects, attachments = await _stores(tmp_path)
    conversation = await chat.create("c")
    held = await attachments.add(
        conversation.id, kind="document", name="a.md", size_bytes=1, document_id=11, data=b"a"
    )
    await attachments.add(
        conversation.id, kind="document", name="b.md", size_bytes=1, document_id=22, data=b"b"
    )

    with attachments.path_for(held.id, "a.md").open("rb"):
        documents = await attachments.purge_for_conversation(conversation.id)

    assert sorted(documents) == [11, 22]
    assert await attachments.list_for(conversation.id) == []
    await mem.store.close()


# -- projects ------------------------------------------------------------------


async def test_a_conversation_is_filed_into_a_project(tmp_path: Path) -> None:
    mem, chat, projects, _attachments = await _stores(tmp_path)
    project = await projects.create("  Roof   repair  ")
    assert project.name == "Roof repair"

    conversation = await chat.create("quote")
    assert await projects.assign(conversation.id, project.id) is True

    assert (await chat.get(conversation.id)).project_id == project.id  # type: ignore[union-attr]
    assert (await projects.list())[0].conversation_count == 1
    await mem.store.close()


async def test_deleting_a_project_keeps_its_conversations(tmp_path: Path) -> None:
    """Deleting a project unfiles its conversations instead of deleting them."""
    mem, chat, projects, _attachments = await _stores(tmp_path)
    project = await projects.create("Temporary")
    conversation = await chat.create("worth keeping")
    await projects.assign(conversation.id, project.id)

    assert await projects.delete(project.id) is True

    survivor = await chat.get(conversation.id)
    assert survivor is not None and survivor.project_id is None  # unfiled, not deleted
    await mem.store.close()


async def test_the_scope_is_the_documents_attached_inside_the_project(tmp_path: Path) -> None:
    mem, chat, projects, attachments = await _stores(tmp_path)
    roof = await projects.create("Roof")
    other = await projects.create("Other")

    inside = await chat.create("roof chat")
    await projects.assign(inside.id, roof.id)
    await attachments.add(inside.id, kind="document", name="a.md", size_bytes=1, document_id=11)

    outside = await chat.create("unfiled chat")
    await attachments.add(outside.id, kind="document", name="b.md", size_bytes=1, document_id=22)

    assert await projects.document_ids_for(roof.id) == [11]
    assert await projects.document_ids_for(other.id) == []  # empty, not "everything"
    await mem.store.close()


async def test_project_scope_narrows_retrieval_at_the_search_not_the_results(
    tmp_path: Path,
) -> None:
    """A scoped search finds the project's chunks even when they aren't the nearest.

    Filtering after the search would return an empty list here.
    """
    mem, _chat, _projects, _attachments = await _stores(tmp_path)
    near = await mem.rag.index_text("Alpha", "alpha alpha alpha alpha")
    far = await mem.rag.index_text("Beta", "beta beta beta beta")

    unscoped = await mem.rag.retrieve("alpha", k=1)
    assert [hit.chunk.document_id for hit in unscoped] == [near.id]  # alpha wins on merit

    scoped = await mem.rag.retrieve("alpha", k=1, document_ids=[far.id])
    assert [hit.chunk.document_id for hit in scoped] == [far.id]
    assert scoped, "a scoped search returned nothing — k was spent before the filter"

    # And a project holding nothing retrieves nothing, rather than falling back to all.
    assert await mem.rag.retrieve("alpha", k=1, document_ids=[]) == []
    await mem.store.close()


# -- deletion cleans up after itself -------------------------------------------


async def test_purging_a_conversation_takes_its_files_and_documents(tmp_path: Path) -> None:
    """Purging a conversation removes its stored files and indexed documents.

    Otherwise its documents would stay retrievable after the conversation is
    deleted, with no surface left to remove them.
    """
    mem, chat, _projects, attachments = await _stores(tmp_path)
    document = await mem.rag.index_text("Secret notes", "the vault code is in here")
    conversation = await chat.create("sensitive")
    await attachments.add(
        conversation.id, kind="document", name="notes.md", size_bytes=5, document_id=document.id
    )
    image = await attachments.add(
        conversation.id, kind="image", name="p.png", size_bytes=3, data=b"png"
    )
    stored = attachments.path_for(image.id, "p.png")
    assert stored.exists()

    # What the delete route does, in order: attachments first, then the conversation.
    document_ids = await attachments.purge_for_conversation(conversation.id)
    for document_id in document_ids:
        await mem.rag.delete(document_id)
    await chat.delete(conversation.id)

    assert document_ids == [document.id]
    assert await attachments.list_for(conversation.id) == []
    assert stored.exists() is False
    assert await mem.rag.list_documents() == []
    assert await mem.rag.retrieve("vault code", k=3) == []  # gone from retrieval, not just hidden
    await mem.store.close()


async def test_purging_a_conversation_without_attachments_is_a_no_op(tmp_path: Path) -> None:
    mem, chat, _projects, attachments = await _stores(tmp_path)
    conversation = await chat.create("plain")
    assert await attachments.purge_for_conversation(conversation.id) == []
    await mem.store.close()
