"""Document search beyond meaning: keyword search, fusion, and heading paths through
retrieval. What a question names and what a turn cites are in test_rag_gate.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlite_vec
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.config_memory import MemorySettings
from app.core.event_bus import InProcessEventBus
from app.services.chat.context import assemble
from app.services.memory import rag_fts
from app.services.memory.models import RagChunk, RetrievedChunk
from app.services.memory.rag import RagService, fuse
from app.services.memory.rag_store import RagStore
from app.services.memory.store import MemoryStore
from tests.fakes import build_fake_memory_runtime, build_fake_memory_stack, build_fake_rag

CAR_LOG = (
    "Car log\n\n"
    "Oil and filter changed, 5W-30, oil filter part number OF-2291.\n\n"
    "Front brake pads replaced, part BP-7730."
)
COFFEE = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]  # the fake embedder's coffee axis
MARKDOWN = "text/markdown"


def _hit(chunk_id: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=RagChunk(id=chunk_id, document_id=1, ordinal=chunk_id, text="text"),
        document_title="notes",
        distance=0.5,
    )


async def _rag(tmp_path: Path, **settings: object) -> tuple[MemoryStore, RagService]:
    store, _, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    config = MemorySettings().model_copy(update=settings)
    return store, await build_fake_rag(store, InProcessEventBus(), config)


async def _add_pathless(store: MemoryStore, title: str, text: str, vector: list[float]) -> None:
    """A chunk stored before 0.22.0: no heading path."""
    async with store.transaction() as db:
        cur = await db.execute(
            "INSERT INTO rag_documents (title, mime, chunk_count, added_at) "
            "VALUES (?, 'text/plain', 1, '2026-01-01T00:00:00+00:00')",
            (title,),
        )
        cur = await db.execute(
            "INSERT INTO rag_chunks (document_id, ordinal, text, created_at) "
            "VALUES (?, 0, ?, '2026-01-01T00:00:00+00:00')",
            (cur.lastrowid, text),
        )
        await db.execute(
            "INSERT INTO rag_chunk_vec (chunk_id, embedding) VALUES (?, ?)",
            (cur.lastrowid, sqlite_vec.serialize_float32(vector)),
        )


def test_the_search_depths_stay_within_what_sqlite_vec_takes() -> None:
    """Past 4,096 neighbours sqlite-vec fails every search; 0 would ask for nothing."""
    for too_far in ({"rag_keyword_depth": 501}, {"rag_top_k": 101}, {"rag_top_k": 0}):
        with pytest.raises(ValidationError):
            MemorySettings(**too_far)


def test_a_question_becomes_a_quoted_match_that_can_run_nothing_else() -> None:
    """Every term quoted, the identifier's first: an operator or a stray quote is only
    text (mutation: unquoted)."""
    assert rag_fts.match_query('part "OF-2291" OR NEAR(x)') == (
        '"OF" OR "2291" OR "part" OR "OR" OR "NEAR" OR "x"'
    )
    assert rag_fts.match_query("?!") == ""  # nothing to search, not everything


def test_the_match_holds_each_term_once_up_to_a_cap() -> None:
    words = [f"w{chr(97 + n // 26)}{chr(97 + n % 26)}" for n in range(50)]

    assert rag_fts.match_query(" ".join(words)).count('"') == 2 * 32
    assert rag_fts.match_query("coffee Coffee coffee") == '"coffee"'  # the index folds case


def test_an_identifier_past_the_cap_is_still_searched() -> None:
    words = [f"w{chr(97 + n // 26)}{chr(97 + n % 26)}" for n in range(40)]

    match = rag_fts.match_query(" ".join(words) + " so what does E15 mean?")

    assert match.startswith('"E15" OR ')


async def test_keyword_search_finds_the_part_number_and_carries_its_distance(
    tmp_path: Path,
) -> None:
    """Measured against the question's meaning, so the floor judges a keyword hit on
    the scale of a nearest one (mutation: no distance)."""
    store, rag = await _rag(tmp_path)
    await rag.index_text("car-log", CAR_LOG)
    await rag.index_text("coffee", "Espresso machine part OF-2291.")

    hits = await RagStore(store, 8).keyword_chunks("What was part OF-2291?", COFFEE, 5)

    by_title = {hit.document_title: hit for hit in hits}
    assert by_title["coffee"].distance == pytest.approx(0.0, abs=1e-6)
    assert by_title["car-log"].distance == float("inf")  # no meaning to compare: far
    await store.close()


async def test_keyword_search_ranks_the_better_match_first(tmp_path: Path) -> None:
    """(mutation: the worst BM25 first)"""
    store, rag = await _rag(tmp_path)
    await rag.index_text("once", "The filter OF-2291 fits the old car and many other cars too.")
    await rag.index_text("twice", "OF-2291: the filter OF-2291.")

    hits = await RagStore(store, 8).keyword_chunks("OF-2291", COFFEE, 5)

    assert [hit.document_title for hit in hits] == ["twice", "once"]
    await store.close()


async def test_keyword_search_folds_accents(tmp_path: Path) -> None:
    store, rag = await _rag(tmp_path)
    await rag.index_text("menu", "Café crème at the corner bakery.")

    hits = await RagStore(store, 8).keyword_chunks("cafe creme", COFFEE, 5)

    assert [hit.document_title for hit in hits] == ["menu"]
    await store.close()


async def test_keyword_search_runs_only_for_a_question_naming_an_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fused into every question it cost paraphrases their first place (mutation: always
    run it); off when its depth is 0 (mutation: ignore the setting)."""
    store, rag = await _rag(tmp_path)
    await rag.index_text("car-log", CAR_LOG)
    asked: list[str] = []
    original = RagStore.keyword_chunks

    async def spy(self: RagStore, question: str, *args: object, **kwargs: object) -> object:
        asked.append(question)
        return await original(self, question, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(RagStore, "keyword_chunks", spy)

    await rag.retrieve("when was the oil changed?")
    found = await rag.retrieve("What was part OF-2291?")
    assert asked == ["What was part OF-2291?"]
    assert "OF-2291" in found[0].chunk.text

    _, off = await _rag(tmp_path / "off", rag_keyword_depth=0)
    await off.retrieve("What was part OF-2291?")
    assert asked == ["What was part OF-2291?"]
    await store.close()


async def test_a_keyword_hit_joins_the_nearest_chunks(tmp_path: Path) -> None:
    """Two deep, meaning alone returns the two coffee notes; the part number comes from
    keyword search (mutation: return the nearest alone)."""
    store, rag = await _rag(tmp_path, rag_keyword_depth=2)
    await rag.index_text("a", "Coffee notes.")
    await rag.index_text("b", "Coffee and rain.")
    await rag.index_text("c", "Mom kept part OF-2291.")

    hits = await rag.retrieve("coffee OF-2291", k=2)

    assert "OF-2291" in "".join(hit.chunk.text for hit in hits)
    await store.close()


async def test_each_search_goes_as_deep_as_the_answer_asked_for(tmp_path: Path) -> None:
    """(mutation: search only `rag_keyword_depth` deep)"""
    store, rag = await _rag(tmp_path, rag_keyword_depth=1)
    for n in range(3):
        await rag.index_text(f"part-{n}", f"Part OF-2291, note {n}, coffee.")

    assert len(await rag.retrieve("OF-2291 coffee", k=3)) == 3
    await store.close()


async def test_a_question_with_nothing_to_match_searches_nothing(tmp_path: Path) -> None:
    """ "___" reads as an identifier but leaves no term, and an empty MATCH is a syntax
    error (mutation: no guard)."""
    store, rag = await _rag(tmp_path)
    await rag.index_text("car-log", CAR_LOG)

    assert rag_fts.identifiers("___?") == ["___"]
    assert isinstance(await rag.retrieve("___?"), list)
    await store.close()


async def test_keyword_search_stays_inside_a_project(tmp_path: Path) -> None:
    store, rag = await _rag(tmp_path)
    await rag.index_text("car-log", CAR_LOG)
    other = await rag.index_text("spares", "Spare oil filter OF-2291 on the garage shelf.")
    keywords = RagStore(store, 8)

    inside = await keywords.keyword_chunks("OF-2291", [0.0] * 8, 5, document_ids=[other.id])
    assert {hit.chunk.document_id for hit in inside} == {other.id}
    assert await keywords.keyword_chunks("OF-2291", [0.0] * 8, 5, document_ids=[]) == []
    await store.close()


async def test_the_keyword_index_follows_a_deleted_document(tmp_path: Path) -> None:
    """(mutation: a delete trigger that does nothing, whose leftover postings the join
    to the chunks would hide)"""
    store, rag = await _rag(tmp_path)
    document = await rag.index_text("car-log", CAR_LOG)

    await rag.delete(document.id)

    assert await RagStore(store, 8).keyword_chunks("OF-2291", [0.0] * 8, 5) == []
    async with store.db.execute("SELECT count(*) FROM rag_chunks_fts_docsize") as cur:
        assert (await cur.fetchone() or (None,))[0] == 0
    await store.close()


async def test_chunks_stored_before_the_index_existed_are_found(tmp_path: Path) -> None:
    """A library from before 0.22.0: the new index starts empty (mutation: no backfill),
    and the table has no heading path column yet."""
    store, _, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    async with store.transaction() as db:
        await db.executescript(
            "CREATE TABLE rag_documents (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT "
            "NULL, source_path TEXT, mime TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT "
            "0, added_at TEXT NOT NULL);"
            "CREATE TABLE rag_chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, document_id "
            "INTEGER NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL, created_at TEXT "
            "NOT NULL);"
            "INSERT INTO rag_documents (title, mime, chunk_count, added_at) "
            "VALUES ('car-log', 'text/plain', 1, '2026-01-01T00:00:00+00:00');"
            "INSERT INTO rag_chunks (document_id, ordinal, text, created_at) "
            "VALUES (1, 0, 'Oil filter part number OF-2291.', '2026-01-01T00:00:00+00:00');"
        )
        await db.execute(
            "CREATE VIRTUAL TABLE rag_chunk_vec USING vec0(chunk_id integer primary key, "
            "embedding float[8] distance_metric=cosine)"
        )
        await db.execute(
            "INSERT INTO rag_chunk_vec (chunk_id, embedding) VALUES (1, ?)",
            (sqlite_vec.serialize_float32([1.0] + [0.0] * 7),),
        )

    await build_fake_rag(store, InProcessEventBus())

    hits = await RagStore(store, 8).keyword_chunks("OF-2291", [0.0] * 8, 5)
    assert [hit.chunk.text for hit in hits] == ["Oil filter part number OF-2291."]
    assert hits[0].chunk.heading_path == ""
    await store.close()


async def test_a_keyword_hit_without_its_vector_is_still_found(tmp_path: Path) -> None:
    """(mutation: an inner join on the vectors)"""
    store, rag = await _rag(tmp_path)
    await rag.index_text("car-log", CAR_LOG)
    async with store.transaction() as db:
        await db.execute("DELETE FROM rag_chunk_vec")

    hits = await RagStore(store, 8).keyword_chunks("OF-2291", [1.0] + [0.0] * 7, 5)

    assert hits and hits[0].distance == float("inf")
    await store.close()


def test_fusion_puts_what_both_searches_found_first() -> None:
    """Found by both beats either search's first place, and a tie keeps the order the
    chunks were first met in (mutation: keep one list's order)."""
    by_meaning = [_hit(1), _hit(2), _hit(3)]
    by_keyword = [_hit(4), _hit(3)]

    assert [hit.chunk.id for hit in fuse([by_meaning, by_keyword])] == [3, 1, 4, 2]
    assert [hit.chunk.id for hit in fuse([by_meaning, []])] == [1, 2, 3]


def test_fusion_damps_ranks_by_sixty() -> None:
    """Sixth in both lists beats first in one at 60; at 1 it wouldn't (mutation: another
    damping)."""
    one = [_hit(1), *(_hit(n) for n in range(10, 14)), _hit(2)]
    other = [_hit(3), *(_hit(n) for n in range(20, 24)), _hit(2)]

    assert fuse([one, other])[0].chunk.id == 2


async def test_the_heading_path_is_embedded_with_the_text(tmp_path: Path) -> None:
    """A chunk whose text never names its subject is found by the heading that does
    (mutation: embed the text alone)."""
    store, rag = await _rag(tmp_path)
    # The fake embedder counts topic words. Without its path the kitchen chunk says only
    # "mom", further from "espresso" than the gutters' "latte"; with it, "coffee" and
    # "espresso" twice over.
    await rag.index_text(
        "kitchen",
        "# Coffee and espresso machine\n\n## Descaling\n\nMom says run it twice.",
        mime=MARKDOWN,
    )
    await rag.index_text(
        "house", "# Gutters\n\n## Clearing\n\nRain runs through, then a latte.", mime=MARKDOWN
    )

    hits = await rag.retrieve("espresso", k=2)

    assert hits[0].chunk.text == "## Descaling\n\nMom says run it twice."
    assert hits[0].distance < hits[1].distance
    await store.close()


async def test_a_chunk_keeps_its_heading_path_through_retrieval(tmp_path: Path) -> None:
    store, rag = await _rag(tmp_path)
    await rag.index_text(
        "recipes", "# Breads\n\n## Focaccia\n\nBake the coffee loaf at 230.", mime=MARKDOWN
    )

    hits = await rag.retrieve("coffee", k=1)

    assert hits[0].chunk.heading_path == "recipes › Breads › Focaccia"
    await store.close()


async def test_a_turn_labels_each_document_with_its_heading_path(tmp_path: Path) -> None:
    """(mutation: label with the document's title alone)"""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await memory.rag.index_text(
        "recipes", "# Breads\n\n## Focaccia\n\nThe coffee crumb loaf.", mime=MARKDOWN
    )

    turn = await assemble(memory, get_settings(), "coffee")

    assert "| recipes › Breads › Focaccia " in turn.documents
    await memory.store.close()


async def test_a_chunk_without_a_path_is_labelled_with_its_title(tmp_path: Path) -> None:
    """A document added before 0.22.0 (mutation: the path alone, empty here)."""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await _add_pathless(memory.store, "old-notes", "Coffee notes from before.", COFFEE)

    turn = await assemble(memory, get_settings(), "coffee")

    assert "<<doc 1 | old-notes " in turn.documents
    await memory.store.close()
