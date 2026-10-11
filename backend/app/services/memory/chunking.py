"""Heading/sentence-aware chunking for RAG indexing.

Splits source text into bounded, overlapping passages that respect heading and
paragraph boundaries, so a retrieved chunk reads as a coherent unit and can be
cited. This is offline indexing — coherence matters more than speed.

In Markdown, each chunk carries the headings it sits under, the document's title first
("family-recipes › Breads › Focaccia"). A section longer than one chunk names its
subject only in its heading, so without the path its second chunk ("Bake at 230 °C for
22 minutes") could belong to any recipe. A heading line leaves the text only where the
path carries it: "# Breads" above "## Focaccia" is no chunk of its own, but "## E14"
straight before "## E15" stays in the text, since the path under E15 no longer holds it.

Other text has no headings: in a .py, .yaml or .sh file a line starting with "#" is a
comment, and in plain text it is as likely a count ("# of guests: 25").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PATH_SEPARATOR = " › "
_PATH_CHARS = 200  # a heading can be any length; the path goes into every chunk's embedding
# A Markdown heading: up to three spaces (four make it code), one to six "#", then a
# space or the end of the line. A closing run of "#" is no part of its words.
_HEADING = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
# A fenced code block opens and closes on a line starting with ``` or ~~~; the lines
# between are code, whatever they start with.
_FENCE = re.compile(r" {0,3}(```|~~~)")
# Latin terminators need trailing whitespace so "3.14" and "Dr." don't split. CJK and
# Arabic terminators don't: 。！？ are not followed by a space, so requiring one would
# make a whole CJK/Arabic document one "sentence" and fall back to blind character
# slicing. Each 1400-char CJK slice exceeds e5's 512-token window, so its tail would be
# stored and citable but never embedded, and so never retrieved.
_SENTENCE = re.compile(r".*?(?:[.!?…](?=\s|$)|[。！？؟۔])", re.DOTALL)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Chunk:
    """A passage, and the headings it starts under, outermost first.

    Not a tuple: `len(chunk)` would then be 2, and a check on a chunk's length would
    pass without measuring anything.
    """

    text: str
    path: str


@dataclass(frozen=True, slots=True)
class _Block:
    text: str
    level: int = 0  # a heading's level; 0 for a paragraph
    words: str = ""  # a heading's words, for the path


def _blocks(text: str, markdown: bool) -> list[_Block]:
    """Paragraphs, and in Markdown each heading line as a block of its own. Every kind
    of line break counts, so a file with old Mac line endings isn't one line."""
    out: list[_Block] = []
    para: list[str] = []
    fence = ""  # the marker of the code block being read

    def end_paragraph() -> None:
        if para:
            out.append(_Block(" ".join(para)))
            para.clear()

    for line in text.splitlines():
        if markdown:
            marker = _FENCE.match(line)
            if fence:
                if marker and marker.group(1) == fence:
                    fence = ""
            elif marker:
                fence = marker.group(1)
            elif heading := _HEADING.match(line):
                end_paragraph()
                words = (heading.group(2) or "").strip()
                if words:  # a bare "#" ends the paragraph and says nothing
                    out.append(_Block(line.strip(), len(heading.group(1)), words))
                continue
        if line.strip():
            para.append(line.strip())
        else:
            end_paragraph()
    end_paragraph()
    return out


def _sentences(text: str) -> list[str]:
    # Matched in place: slicing off each sentence copied the rest of the paragraph every
    # time, which took 40 s on a 4 MB paragraph (a PDF often reads as one).
    out: list[str] = []
    position, end = 0, len(text)
    while position < end:
        if text[position].isspace():
            position += 1
            continue
        match = _SENTENCE.match(text, position)
        if match is None:
            out.append(text[position:].strip())
            break
        out.append(match.group().strip())
        position = match.end()
    return [sentence for sentence in out if sentence]


def _carry(current: str, overlap: int, addition: int, max_chars: int) -> str:
    """The overlap tail to keep, trimmed so tail + next block still fits `max_chars`.

    A fixed `overlap` on top of a block that is already near the limit would push the
    chunk past `max_chars`.
    """
    room = max_chars - addition - 1
    # `overlap <= 0` must return nothing. `s[-0:]` is the whole string, so without this
    # check an overlap of 0 would carry every previous chunk forward (quadratic growth,
    # far past `max_chars`).
    if room <= 0 or overlap <= 0:
        return ""
    return current[-min(overlap, room) :]


def _split_long(block: str, target: int, overlap: int, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for raw in _sentences(block):
        sentence = raw
        # `overlap >= max_chars` would make this slice a no-op and loop forever while
        # appending a chunk each pass; clamp rather than trust config.
        step_back = min(overlap, max_chars // 2)
        while len(sentence) > max_chars:  # a single run with no sentence breaks
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars - step_back :]
        if current and len(current) + len(sentence) + 1 > target:
            chunks.append(current.strip())
            current = _carry(current, overlap, len(sentence), max_chars)
        current += sentence + " "
    if current.strip():
        chunks.append(current.strip())
    return chunks


class _Sections:
    """The headings above the text being read, and the heading lines not yet in a chunk."""

    def __init__(self, title: str) -> None:
        self._title = _SPACE.sub(" ", title).strip()
        self._stack: list[tuple[int, str]] = []
        self.path = self._title[:_PATH_CHARS]
        self._whole = len(self._title) <= _PATH_CHARS
        self._waiting: list[tuple[tuple[int, str], str]] = []

    def enter(self, block: _Block) -> None:
        while self._stack and self._stack[-1][0] >= block.level:
            self._stack.pop()
        heading = (block.level, block.words)
        self._stack.append(heading)
        words = [part for _, part in self._stack]
        joined = PATH_SEPARATOR.join(part for part in (self._title, *words) if part)
        self.path, self._whole = joined[:_PATH_CHARS], len(joined) <= _PATH_CHARS
        # A waiting line the new path holds whole can go; a heading closed with no text
        # under it ("## E14" before "## E15") stays, or its words would be lost.
        self._waiting = [(above, line) for above, line in self._waiting if not self._holds(above)]
        self._waiting.append((heading, block.text))

    def _holds(self, heading: tuple[int, str]) -> bool:
        return self._whole and heading in self._stack

    def waiting(self) -> str:
        """The heading lines the next chunk starts with."""
        return "".join(line + "\n\n" for _, line in self._waiting)

    def would_lose(self) -> bool:
        """Whether leaving the waiting lines out would lose words the path doesn't hold."""
        return not all(self._holds(above) for above, _ in self._waiting)

    def said(self) -> None:
        self._waiting.clear()


def chunk_text(
    text: str,
    *,
    title: str = "",
    target_chars: int = 800,
    overlap_chars: int = 120,
    max_chars: int = 1400,
    markdown: bool = True,
) -> list[Chunk]:
    """Coherent, bounded, overlapping chunks, a new one at each heading, each with the
    heading path it starts under. `markdown=False` reads no headings."""
    chunks: list[Chunk] = []
    sections = _Sections(title)
    current = ""
    said = False  # whether `current` holds more than heading lines

    def flush() -> None:
        nonlocal current, said
        if said and current.strip():
            chunks.append(Chunk(current.strip(), sections.path))
        current, said = "", False

    for block in _blocks(text, markdown):
        if block.level:
            flush()
            sections.enter(block)
            current = sections.waiting()
            continue
        if len(block.text) > max_chars:
            lead = "" if said else current  # the heading lines start its first piece
            flush()
            pieces = _split_long(lead + block.text, target_chars, overlap_chars, max_chars)
            chunks.extend(Chunk(piece, sections.path) for piece in pieces)
            sections.said()
            continue
        if said and len(current) + len(block.text) + 2 > target_chars:
            carried = _carry(current, overlap_chars, len(block.text) + 2, max_chars)
            flush()
            current = carried
        elif not said and len(current) + len(block.text) + 2 > max_chars:
            # The heading lines and the paragraph don't fit one chunk. Lines the path
            # holds can go; any it doesn't are a chunk of their own rather than lost.
            if sections.would_lose():
                chunks.append(Chunk(current.strip(), sections.path))
            current = ""
        current += block.text + "\n\n"
        said = True
        sections.said()
    if not said and current.strip():
        # Headings with no text after them: nothing later holds their words.
        chunks.append(Chunk(current.strip(), sections.path))
    flush()
    return chunks
