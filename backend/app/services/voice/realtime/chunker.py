"""Sentence/clause chunker: turn the LLM token stream into speakable chunks early.

The goal is latency, not perfect segmentation: flush a chunk as soon as it's
speakable so TTS can start, with a `max_chars` fallback so a long clause without
terminal punctuation still flushes. With `clause_chunking` on, the first clause
boundary still in the buffer (a comma, semicolon, colon or dash, then a space) flushes
too when the clause up to it is at least `min_clause_chars` long, so the first audio
comes from a short opening clause rather than a whole sentence. A shorter first clause
isn't sent alone, and the chunk waits for the sentence end. The TTS adapter
re-sanitizes and re-splits, so coarse chunks are fine.

A boundary counts only once text has arrived after it. The end of the buffer is the end
of what has streamed so far, not of the reply: the model sends "3", "." and "30", and
at the second token "It's 3." looked like a sentence and was spoken as one. `flush`
owns the real end. A full stop that belongs to an abbreviation ("Dr.") isn't a
boundary either.
"""

from __future__ import annotations

import re

from app.adapters.tts.text import ends_on_abbreviation

# A terminator, with any closing quote or bracket, that whitespace has followed.
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)")
# A clause boundary, likewise once text follows it: "1,250" streams as "1", ",", "250".
_CLAUSE_END = re.compile(r"[,;:—–](?=\s)")
# A terminator the buffer ends on: a sentence end, once the next word shows it isn't a
# decimal point.
_PENDING_END = re.compile(r"[.!?…]+[\"'”’)\]]*$")


def sentence_end(text: str, start: int = 0) -> int | None:
    """Where the first finished sentence in `text` ends, among the boundaries that end
    at or after `start`; None if there is none yet.

    Shared with the reply cap (`reply_cap.within`), so a capped reply ends exactly where
    the chunker would end a sentence.
    """
    for match in _SENTENCE_END.finditer(text):
        if match.end() >= start and not ends_on_abbreviation(text[: match.end()]):
            return match.end()
    return None


class SentenceChunker:
    """Accumulate streamed tokens; emit chunks at sentence (and optional clause) ends."""

    def __init__(
        self,
        max_chars: int = 220,
        *,
        clause_chunking: bool = False,
        min_clause_chars: int = 16,
    ) -> None:
        self._max_chars = max_chars
        self._clause_chunking = clause_chunking
        self._min_clause_chars = min_clause_chars
        self._buffer = ""
        self._spoken: list[str] = []

    def push(self, text: str) -> list[str]:
        """Add a token delta; return any chunks now ready to synthesize."""
        self._buffer += text
        chunks: list[str] = []
        while True:
            chunk = self._next_chunk()
            if chunk is None:
                break
            if chunk:
                chunks.append(chunk)
        self._spoken.extend(chunks)
        return chunks

    def _next_chunk(self) -> str | None:
        end = sentence_end(self._buffer)
        if end is not None:
            return self._take(end)
        if self._clause_chunking:
            clause = _CLAUSE_END.search(self._buffer)
            opening = self._buffer[: clause.end()].strip() if clause is not None else ""
            if clause is not None and len(opening) >= self._min_clause_chars:
                return self._take(clause.end())
        # Not while a sentence end waits for its next word: cut at the last space, it
        # sent a long sentence's last word ("here.") as a chunk of its own.
        if len(self._buffer) >= self._max_chars and not _PENDING_END.search(self._buffer):
            return self._take(self._safe_cut())
        return None

    def _take(self, end: int) -> str:
        chunk = self._buffer[:end].strip()
        self._buffer = self._buffer[end:].lstrip()
        return chunk

    def flush(self) -> list[str]:
        """End of stream: emit whatever non-whitespace remains."""
        tail = self._buffer.strip()
        self._buffer = ""
        if not tail:
            return []
        self._spoken.append(tail)
        return [tail]

    def reset(self) -> None:
        self._buffer = ""
        self._spoken = []

    @property
    def spoken_text(self) -> str:
        """Everything emitted so far (for the assistant turn in history)."""
        return " ".join(self._spoken)

    def _safe_cut(self) -> int:
        window = self._buffer[: self._max_chars]
        space = window.rfind(" ")
        return space if space > 0 else self._max_chars
