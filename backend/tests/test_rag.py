"""RAG path: index (txt/md/pdf) + cited retrieval + delete. Chunking is in
test_chunking.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.memory import cipher
from app.services.memory import rag as rag_module
from app.services.memory.rag import RagError, RagService
from app.services.memory.store import MemoryStore
from tests.fakes import build_fake_memory_stack, build_fake_rag


async def _rag(tmp_path: Path) -> tuple[MemoryStore, RagService]:
    bus = InProcessEventBus()
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    return store, await build_fake_rag(store, bus)


async def _stored(store: MemoryStore, document_id: int) -> list[tuple[str, str]]:
    """A document's chunks as stored: (text, heading path)."""
    async with store.db.execute(
        "SELECT text, heading_path FROM rag_chunks WHERE document_id = ? ORDER BY ordinal",
        (document_id,),
    ) as cur:
        return [(str(row[0]), str(row[1])) for row in await cur.fetchall()]


def _minimal_pdf(text: str) -> bytes:
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode() + b">>stream\n" + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj" + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer<</Size " + str(len(objects) + 1).encode() + b"/Root 1 0 R>>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    return bytes(out)


async def test_a_document_whose_write_is_cut_short_leaves_nothing_behind(tmp_path: Path) -> None:
    """Its row and its chunks are written together, so a failure part-way (or the stop
    that cancels a document still being read) never lists a document with nothing in it
    (mutation: the row in one transaction and the chunks in another)."""
    store, rag = await _rag(tmp_path)
    real_embed = rag._embedder.embed

    async def one_bad_vector(texts: list[str]) -> list[list[float]]:
        vectors = await real_embed(texts)
        return [*vectors[:1], vectors[1][:-1], *vectors[2:]]  # the second one short

    rag._embedder.embed = one_bad_vector  # type: ignore[method-assign]
    text = "\n\n".join(f"Paragraph {n}. " + "Some words about the garden. " * 40 for n in range(6))

    with pytest.raises(cipher.SQLITE_ERRORS):
        await rag.index_text("Garden notes", text)

    async with store.db.execute("SELECT COUNT(*) FROM rag_documents") as cur:
        (documents,) = await cur.fetchone() or (None,)
    assert documents == 0
    await store.close()


async def test_index_text_and_retrieve_with_citation(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    rag = await build_fake_rag(store, bus)
    await rag.index_text(
        "Sourdough Guide", "Feeding a sourdough starter. Feed the starter carefully."
    )
    await rag.index_text("Weather Notes", "The weather is cold today. Rain is coming.")

    hits = await rag.retrieve("sourdough starter", k=3)
    assert hits
    assert "sourdough" in hits[0].chunk.text.lower()
    assert hits[0].document_title == "Sourdough Guide"  # citation carries the source title
    assert hits[0].chunk.ordinal == 0
    await store.close()


async def test_index_file_txt_and_md(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    rag = await build_fake_rag(store, bus)
    txt = tmp_path / "notes.txt"
    txt.write_text("Sourdough baking guide.", encoding="utf-8")
    md = tmp_path / "guide.md"
    md.write_text("# Coffee\n\nEspresso and latte notes.", encoding="utf-8")

    doc_txt = await rag.index_file(txt)
    doc_md = await rag.index_file(md)
    assert doc_txt.mime == "text/plain"
    assert doc_md.mime == "text/markdown"
    assert {doc.title for doc in await rag.list_documents()} == {"notes", "guide"}
    await store.close()


async def test_a_file_path_is_recorded_only_when_the_caller_supplies_one(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    rag = await build_fake_rag(store, bus)
    note = tmp_path / "notes.txt"
    note.write_text("Espresso and latte notes.", encoding="utf-8")

    unnamed = await rag.index_file(note)
    named = await rag.index_file(note, title="Coffee", source_path="notes/coffee.txt")
    await store.close()
    assert unnamed.source_path is None
    assert named.source_path == "notes/coffee.txt"


async def test_index_pdf_and_retrieve(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    rag = await build_fake_rag(store, bus)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_minimal_pdf("Sourdough starter dough guide"))

    doc = await rag.index_file(pdf)
    assert doc.mime == "application/pdf"
    assert doc.chunk_count >= 1
    hits = await rag.retrieve("sourdough starter")
    assert any("sourdough" in hit.chunk.text.lower() for hit in hits)
    await store.close()


async def test_delete_document_removes_chunks(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    rag = await build_fake_rag(store, bus)
    doc = await rag.index_text("Sourdough Guide", "Sourdough starter dough notes.")
    assert await rag.retrieve("sourdough")

    await rag.delete(doc.id)
    assert await rag.list_documents() == []
    assert await rag.retrieve("sourdough") == []  # chunks + vectors gone
    await store.close()


async def test_emits_rag_updated(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    seen: list[Event] = []

    async def collect(event: Event) -> None:
        seen.append(event)

    bus.subscribe("rag.updated", collect)
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    rag = await build_fake_rag(store, bus)
    await rag.index_text("Doc", "Sourdough baking.")
    assert any(event.type == "rag.updated" for event in seen)
    await store.close()


async def test_only_markdown_is_read_for_headings(tmp_path: Path) -> None:
    """A comment in a script is text, not a heading (mutation: read every file as
    Markdown)."""
    store, rag = await _rag(tmp_path)

    guide = await rag.index_text(
        "guide", "# Coffee\n\n## Descaling\n\nRun it twice.", mime="text/markdown"
    )
    script = await rag.index_text("script", "# Load the data\n# Then clean it\nrows = load()")

    assert await _stored(store, guide.id) == [
        ("## Descaling\n\nRun it twice.", "guide › Coffee › Descaling")
    ]
    assert await _stored(store, script.id) == [
        ("# Load the data # Then clean it rows = load()", "script")
    ]
    await store.close()


async def test_a_heading_that_looks_like_a_secret_leaves_the_path(tmp_path: Path) -> None:
    """The path would carry it into the embedding and the label of every chunk under it.
    Those chunks stay; the one holding the heading line goes (mutations: check the text
    alone; drop every chunk under it)."""
    store, rag = await _rag(tmp_path)
    paragraph = "The router sits in the hall cupboard. " * 10
    notes = (
        "# House notes\n\n## Router password: FAKE-pass-0000\n\n"
        + "\n\n".join([paragraph] * 3)
        + "\n\n## Garden\n\nWater the coffee plants at night."
    )

    document = await rag.index_text("house", notes, mime="text/markdown")

    stored = await _stored(store, document.id)
    assert not any("FAKE-pass" in text + path for text, path in stored)
    # The section's first chunk holds the heading line; its second is kept.
    assert [path for _, path in stored] == ["house › House notes", "house › House notes › Garden"]
    assert "hall cupboard" in stored[0][0]
    await store.close()


async def test_a_title_the_secrets_check_misreads_leaves_the_document_whole(
    tmp_path: Path,
) -> None:
    """ "Passwords 101" heads every chunk's path; before, that refused the whole document
    as credentials."""
    store, rag = await _rag(tmp_path)

    document = await rag.index_text("Passwords 101", "Pick long ones and never reuse them.")

    assert await _stored(store, document.id) == [("Pick long ones and never reuse them.", "")]
    await store.close()


async def test_headings_that_look_like_a_secret_only_together_leave_no_path(
    tmp_path: Path,
) -> None:
    """Neither heading alone reads as a secret, the path joining them does (mutation:
    keep what is left once each flagged heading is out)."""
    store, rag = await _rag(tmp_path)

    document = await rag.index_text(
        "t",
        "## Router password\n\n### FAKE-pass-0000\n\nAsk before sharing it.",
        mime="text/markdown",
    )

    assert [path for _, path in await _stored(store, document.id)] == [""]
    await store.close()


async def test_a_byte_order_mark_does_not_hide_the_first_heading(tmp_path: Path) -> None:
    store, rag = await _rag(tmp_path)
    note = tmp_path / "guide.md"
    note.write_text("\ufeff# Coffee\n\nEspresso notes.", encoding="utf-8")

    document = await rag.index_file(note)

    assert await _stored(store, document.id) == [("# Coffee\n\nEspresso notes.", "guide › Coffee")]
    await store.close()


@pytest.mark.parametrize("text", ["x" * 33, "é" * 17])  # 33 characters; 34 bytes in UTF-8
async def test_a_text_past_the_limit_is_refused_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """Pasted text had no limit; its bytes count, not its characters (mutations: no
    check; count characters only)."""
    monkeypatch.setattr(rag_module, "MAX_TEXT_BYTES", 32)
    store, rag = await _rag(tmp_path)

    with pytest.raises(RagError) as refused:
        await rag.index_text("Pasted", text)

    assert refused.value.code == "text_too_long"
    assert await rag.list_documents() == []
    await store.close()


async def test_a_file_whose_text_is_over_the_limit_says_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uploaded, not pasted: "that text" named something the user never sent
    (mutation: the pasted text's words)."""
    monkeypatch.setattr(rag_module, "MAX_TEXT_BYTES", 32)
    store, rag = await _rag(tmp_path)
    notes = tmp_path / "notes.txt"
    notes.write_text("A long list of things to do. " * 4, encoding="utf-8")

    with pytest.raises(RagError) as refused:
        await rag.index_file(notes)

    assert refused.value.code == "text_too_long"
    assert str(refused.value) == "That file holds more text than I can take (25 MB)."
    await store.close()
