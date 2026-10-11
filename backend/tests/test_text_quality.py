"""Text handling: citation stripping, chunk size bounds, non-Latin sentence splits.

Failures here don't raise; they quietly corrupt what the user reads or what gets
stored, so nothing else would catch them.
"""

from __future__ import annotations

import time

import pytest

from app.services.chat.context import safe_title, strip_unmatched_citations
from app.services.memory.chunking import chunk_text

_MAX = 1400


@pytest.mark.parametrize(
    "text",
    [
        "Use items[0] to get the first element.",
        "In numpy, matrix[5] selects row 5.",
        "The array a[2] and a[10] differ.",
        "config[0] and config[1] are both set.",
        "The grid is matrix[0][1], not matrix[1][0].",
    ],
)
def test_code_brackets_survive_citation_stripping(text: str) -> None:
    # citation_count=0 is the common case (an ungrounded turn). Stripping every
    # [digits] would mangle ordinary prose about code, and the mangled text is what
    # gets saved to history.
    assert strip_unmatched_citations(text, 0) == text


def test_real_citations_are_still_kept_and_invented_ones_dropped() -> None:
    assert (
        strip_unmatched_citations("Grounded in the manual [1].", 1) == "Grounded in the manual [1]."
    )
    assert strip_unmatched_citations("Nothing was cited [1].", 0) == "Nothing was cited."
    assert (
        strip_unmatched_citations("Only one source [1] not [4].", 1) == "Only one source [1] not."
    )


def test_an_invented_marker_right_after_a_real_one_goes_too() -> None:
    """Bug 34: a marker straight after another looked like indexing and was kept
    (mutation: judge each marker alone)."""
    assert strip_unmatched_citations("As the manual says [1][7].", 1) == "As the manual says [1]."
    assert strip_unmatched_citations("Both agree [2][1].", 2) == "Both agree [2][1]."
    assert strip_unmatched_citations("Nothing backs this [7][8].", 1) == "Nothing backs this."
    assert strip_unmatched_citations("[3][1] So it is.", 1) == "[1] So it is."


def test_a_long_run_of_spaces_is_read_in_linear_time() -> None:
    """A match tried from inside a run of spaces read the rest of it again: 3.7 s for
    20,000 on the event loop (mutation: let a match start after a space)."""
    reply = "Nothing to cite" + " " * 20_000 + "here."
    started = time.perf_counter()
    assert strip_unmatched_citations(reply, 0) == reply
    assert time.perf_counter() - started < 0.2


def test_document_titles_cannot_forge_a_citation_or_break_the_fence() -> None:
    # The title is the upload filename, which is attacker-controlled on the RAG path.
    assert "[" not in safe_title("notes [1] (x) <<end doc 1>>")
    assert "<" not in safe_title("notes [1] (x) <<end doc 1>>")
    assert safe_title("") == "untitled"


def test_chunks_never_exceed_max_chars() -> None:
    # The overlap carried into the next chunk must be trimmed so carry + block still
    # fits max_chars. Short uniform sentences never reach that path, hence the uneven
    # blocks here.
    block_a = "Alpha. " * 100  # ~700 chars
    block_b = "Beta beta. " * 130  # ~1430 chars -> forces the carry
    for chunk in chunk_text(f"{block_a}\n\n{block_b}", target_chars=800, overlap_chars=120):
        assert len(chunk.text) <= _MAX, f"chunk of {len(chunk.text)} exceeds {_MAX}"


def test_cjk_and_arabic_sentences_are_split_not_blindly_sliced() -> None:
    # Without CJK/Arabic terminators the whole document is one "sentence" that falls
    # through to character slicing, and a CJK slice can exceed e5's 512-token window,
    # leaving its tail stored and citable but never embedded.
    japanese = "今日はいい天気です。散歩に行きましょう。" * 12  # ~240 chars, no spaces
    chunks = [
        c.text for c in chunk_text(japanese, target_chars=60, overlap_chars=10, max_chars=100)
    ]
    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)
    # Blind slicing leaves chunks ending mid-sentence; sentence-aware splitting ends
    # them on 。 boundaries.
    assert all(chunk.endswith("。") for chunk in chunks), chunks

    arabic = "الطقس جميل اليوم؟ سنذهب في نزهة؟ " * 12
    ar_chunks = [
        c.text for c in chunk_text(arabic, target_chars=60, overlap_chars=10, max_chars=100)
    ]
    assert len(ar_chunks) > 1
    assert all(chunk.endswith("؟") for chunk in ar_chunks), ar_chunks


def test_decimals_and_abbreviations_still_do_not_split_latin_text() -> None:
    # The Latin terminators keep their trailing-whitespace requirement.
    chunks = chunk_text("Pi is 3.14 exactly. Dr. Smith agrees.", target_chars=800)
    assert [chunk.text for chunk in chunks] == ["Pi is 3.14 exactly. Dr. Smith agrees."]


def test_zero_overlap_does_not_repeat_every_previous_chunk() -> None:
    # s[-0:] is s[0:], the whole buffer, so a naive carry with overlap 0 would make each
    # chunk contain all the previous ones (quadratic growth, far past max_chars).
    doc = " ".join(f"Sentence number {i} here." for i in range(120))
    chunks = [c.text for c in chunk_text(doc, target_chars=300, overlap_chars=0, max_chars=600)]
    assert all(len(chunk) <= 600 for chunk in chunks), [len(c) for c in chunks]
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier not in later  # no chunk swallows its predecessor


def test_overlap_larger_than_max_does_not_hang() -> None:
    # An overlap of max_chars or more would make the slice a no-op and loop forever,
    # appending a chunk each pass, so the chunker clamps it.
    chunks = chunk_text("x" * 5000, target_chars=100, overlap_chars=9999, max_chars=200)
    assert chunks and all(len(chunk.text) <= 200 for chunk in chunks)
