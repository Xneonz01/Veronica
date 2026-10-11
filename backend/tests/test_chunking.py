"""Chunking: bounded passages, heading paths in Markdown, and no text lost on the way."""

from __future__ import annotations

import time

import pytest

from app.services.memory.chunking import Chunk, chunk_text


def test_chunk_text_bounds_and_splits_long_block() -> None:
    text = "# Title\n\n" + ("Sentence about dough baking. " * 60)
    chunks = chunk_text(text, target_chars=200, overlap_chars=40, max_chars=400)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 400 for chunk in chunks)
    assert {chunk.path for chunk in chunks} == {"Title"}  # the heading, kept for every piece
    assert chunks[0].text.startswith("# Title\n\nSentence")  # and its line starts the first


def test_chunk_text_starts_new_chunk_at_heading() -> None:
    text = "# A\n\nalpha content here.\n\n# B\n\nbeta content here."
    chunks = chunk_text(text, target_chars=2000)
    assert chunks == [
        Chunk("# A\n\nalpha content here.", "A"),
        Chunk("# B\n\nbeta content here.", "B"),
    ]


def test_chunk_text_hard_caps_runaway_text() -> None:
    # A long run with no sentence/heading boundaries must still be bounded.
    chunks = chunk_text("x" * 5000, target_chars=200, overlap_chars=40, max_chars=400)
    assert chunks
    assert all(len(chunk.text) <= 400 for chunk in chunks)


def test_each_chunk_carries_the_headings_it_sits_under() -> None:
    """A long section names its subject only in its heading, so its second chunk needs
    the path (mutation: leave the path empty)."""
    method = "Stretch and fold the dough four times, twenty minutes apart. " * 6
    text = f"## Breads\n\n### Focaccia\n\nFlour, water, salt and yeast.\n\n{method}\n\nBake at 230."
    chunks = chunk_text(text, title="recipes", target_chars=300, overlap_chars=40)

    assert len(chunks) >= 2
    assert {chunk.path for chunk in chunks} == {"recipes › Breads › Focaccia"}
    assert "Focaccia" not in chunks[-1].text and "Bake at 230." in chunks[-1].text


def test_a_heading_above_a_deeper_one_is_no_chunk_of_its_own() -> None:
    """It says nothing but its words, and the path carries those (mutation: keep it)."""
    chunks = chunk_text("# Recipes\n\n## Breads\n\n### Focaccia\n\nFlour and water.")

    assert chunks == [Chunk("### Focaccia\n\nFlour and water.", "Recipes › Breads › Focaccia")]


def test_a_heading_at_a_higher_level_closes_the_ones_below_it() -> None:
    text = "## Setup\n\n### Wiring\n\nRed to red.\n\n## Care\n\nWipe it monthly."
    chunks = chunk_text(text)

    assert [chunk.path for chunk in chunks] == ["Setup › Wiring", "Care"]


def test_a_heading_that_would_push_a_chunk_past_the_cap_stays_in_the_path_only() -> None:
    text = "## Setup\n\n" + "a" * 395
    chunks = chunk_text(text, target_chars=200, overlap_chars=40, max_chars=400)

    assert chunks == [Chunk("a" * 395, "Setup")]


def test_a_heading_path_is_capped() -> None:
    chunks = chunk_text("# " + "Long heading " * 40 + "\n\nText.", title="notes")

    assert len(chunks[0].path) == 200


def test_a_heading_closed_with_nothing_under_it_keeps_its_words() -> None:
    """The path under "## Printer" doesn't hold the heading before it, so the text must
    (mutation: drop every heading line that has no text under it)."""
    text = "## Wi-Fi guest code rotates monthly\n\n## Printer\n\nToner is in the drawer."

    chunks = chunk_text(text, title="notes")

    assert chunks == [Chunk(text, "notes › Printer")]


def test_a_list_of_headings_loses_none_of_them() -> None:
    codes = "# Error codes\n## E15: drip tray full\n## E16: water tank empty\n## E17: grounds full"
    kept = "## E15: drip tray full\n\n## E16: water tank empty\n\n## E17: grounds full"
    path = "brew › Error codes › E17: grounds full"

    assert chunk_text(codes + "\n\nCall support.", title="brew") == [
        Chunk(f"{kept}\n\nCall support.", path)
    ]
    # With no text under them at all, the headings are the document.
    assert chunk_text(codes, title="brew") == [Chunk(kept, path)]


def test_a_heading_at_the_end_is_kept() -> None:
    chunks = chunk_text("Body text.\n\n## Contact code E15-SUPPORT", title="t")

    assert [chunk.text for chunk in chunks] == ["Body text.", "## Contact code E15-SUPPORT"]


def test_headings_too_long_to_share_a_chunk_are_one_of_their_own() -> None:
    """Only when the path doesn't hold them (mutation: drop them whenever they don't fit)."""
    text = "## E14\n\n## E15\n\n" + "y" * 395

    chunks = chunk_text(text, target_chars=200, overlap_chars=40, max_chars=400)

    assert chunks == [Chunk("## E14\n\n## E15", "E15"), Chunk("y" * 395, "E15")]


def test_a_heading_the_capped_path_cuts_stays_in_the_text() -> None:
    heading = "## " + "The float switch and the tank " * 8
    chunks = chunk_text(heading + "\n\n" + "Short sentence here. " * 90, title="manual")

    assert len(chunks[0].path) == 200
    assert chunks[0].text.startswith(heading.strip())


def test_a_parent_heading_the_capped_path_cuts_stays_above_its_child() -> None:
    """A deeper heading lets its parent's line go only when the path holds all of it
    (mutation: any parent on the path)."""
    parent = "## " + "The float switch and the tank " * 8

    chunks = chunk_text(parent + "\n\n### Care\n\nWipe it monthly.", title="manual")

    assert chunks[0].text == f"{parent.strip()}\n\n### Care\n\nWipe it monthly."


def test_without_markdown_a_hash_line_is_text() -> None:
    """A comment in a .py or .yaml file, or "# of guests" in a note (mutation: read
    headings everywhere)."""
    source = "# Copyright 2026\n# Licensed under MIT\n# Computes the thing.\nimport os\n"

    chunks = chunk_text(source, title="mod", markdown=False)

    assert chunks == [
        Chunk("# Copyright 2026 # Licensed under MIT # Computes the thing. import os", "mod")
    ]


def test_a_hash_line_in_a_code_block_is_no_heading() -> None:
    readme = (
        "# Readme\n\n## Install\n\n```bash\n# clone the repo\ngit clone x\n```\n\n"
        "## Flags\n\nUse --fast."
    )

    chunks = chunk_text(readme, title="readme")

    assert [chunk.path for chunk in chunks] == [
        "readme › Readme › Install",
        "readme › Readme › Flags",
    ]
    assert "# clone the repo" in chunks[0].text


@pytest.mark.parametrize("line", ["    # indented four", "\t# after a tab", "#hashtag"])
def test_what_markdown_does_not_call_a_heading(line: str) -> None:
    chunks = chunk_text(f"# Notes\n\n{line}", title="t")

    assert chunks == [Chunk(f"# Notes\n\n{line.strip()}", "t › Notes")]


def test_old_mac_line_endings_are_line_breaks() -> None:
    chunks = chunk_text("# Oven\r\rBake the focaccia at 230 C for 22 minutes.", title="t")

    assert chunks == [Chunk("# Oven\n\nBake the focaccia at 230 C for 22 minutes.", "t › Oven")]


def test_a_closing_run_of_hashes_and_a_bare_hash_say_nothing() -> None:
    chunks = chunk_text("## Title ##\n\n#\n\nText under it.", title="t")

    assert chunks == [Chunk("## Title ##\n\nText under it.", "t › Title")]


def test_a_long_paragraph_is_split_in_linear_time() -> None:
    """A PDF often reads as one paragraph. Slicing off each sentence copied the rest of
    it every time: 4 MB took 40 s, and this one about 10."""
    paragraph = "One sentence of a long paragraph. " * 60_000

    started = time.perf_counter()
    chunks = chunk_text(paragraph, markdown=False)

    assert time.perf_counter() - started < 2.0
    assert len(chunks) > 1000
