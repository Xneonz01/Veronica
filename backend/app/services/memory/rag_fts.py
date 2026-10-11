"""Keyword search over document chunks: an FTS5 index, BM25, and the query it runs.

The keyword half of document search (`rag.py` fuses it with the meaning half). Measured
on the retrieval evaluation (`eval/retrieval`): meaning alone finds an identifier's
passage poorly. "What was part OF-2291?" scored 0.06 on the rescaled cosine scale, below
the relevance floor, and "What does E15 mean?" put a different manual's error codes
first. A code, a part number or an address means something only as a string, and an
embedding blurs it into the sentences around it.

External content (`content='rag_chunks'`): the index holds postings, not a second copy
of every chunk. Triggers keep it in step. Deletes go through FTS5's `'delete'` command,
which needs the old text, since a plain DELETE on an external-content table leaves
postings that match rows that are gone. Nothing updates a chunk in place, so there is
no update trigger.

`unicode61` splits on `-`, `.` and `_`, which is what lets "OF-2291" and an address
match whatever way the question writes them. Diacritics are folded; there is no
stemming, which would help English and mangle every other language a document can be in.
"""

from __future__ import annotations

import re
from itertools import chain

import aiosqlite

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
  text,
  heading_path,
  content='rag_chunks',
  content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS rag_chunks_fts_ai AFTER INSERT ON rag_chunks BEGIN
  INSERT INTO rag_chunks_fts(rowid, text, heading_path)
  VALUES (new.id, new.text, new.heading_path);
END;
CREATE TRIGGER IF NOT EXISTS rag_chunks_fts_ad AFTER DELETE ON rag_chunks BEGIN
  INSERT INTO rag_chunks_fts(rag_chunks_fts, rowid, text, heading_path)
  VALUES ('delete', old.id, old.text, old.heading_path);
END;
"""

# A term is a run of letters or digits, which is how `unicode61` splits the chunks too.
# Anything else, quotes and FTS5 operators included, is a separator.
_TERM = re.compile(r"[^\W_]+")
_MAX_TERMS = 32  # a pasted paragraph as a question: bounded before it is slow
# A word of a question that only means something as a string: letters and digits
# together (E15, OF-2291, 5W-30), numbers joined by two dots or more (an address, a
# version), an underscore (TIDEPOOL_LOG), a capital inside a word (timeBeginPeriod) or
# three capitals or more (DESC). Not a number with a unit or an ordinal after it
# ("10kg", "3pm", "2nd"), a plain number or decimal ("2025", "1.5"), or "Wi-Fi".
_WORD = re.compile(r"\w[\w.-]*\w|\w")
# Anchored: unanchored, the lookaheads rescanned the rest of the word from every
# position, and 16,000 digits in a question took 1.8 s on the event loop.
_IDENTIFIER = re.compile(r"^(?=.*\d)(?=.*[^\W\d_])|\d\.\d+\.\d|_|[a-z][A-Z]")
_ACRONYM = re.compile(r"[A-Z]{3,}")
_QUANTITY = re.compile(r"\d+[a-z]{1,3}")
_MOST = 16  # identifiers per question; a pasted log names hundreds


def identifiers(question: str) -> list[str]:
    """The identifiers a question names, each once. Keyword search joins the search by
    meaning only for these (`RagService.retrieve`), and a passage naming one is
    relevant whatever its cosine (`chat/context.grounding`).

    In a question written in capitals, capitals make no acronym: "WHEN IS MY DENTIST
    APPOINTMENT?" names nothing.
    """
    letters = [ch for ch in question if ch.isalpha()]
    shouted = sum(ch.isupper() for ch in letters) * 2 > len(letters)
    found: dict[str, None] = {}
    for word in _WORD.findall(question):
        if _QUANTITY.fullmatch(word):
            continue
        if _IDENTIFIER.search(word) or (not shouted and _ACRONYM.fullmatch(word)):
            found.setdefault(word)
            if len(found) == _MOST:
                break
    return list(found)


def names(text: str, identifier: str) -> bool:
    """Whether `text` holds `identifier` as a whole word: "E15" is not in "E150". A code
    matches in any case ("e15"); a word of letters alone only as written, since "DESC"
    is an acronym and "desc" isn't."""
    flags = re.IGNORECASE if any(ch.isdigit() or ch in "_." for ch in identifier) else 0
    pattern = rf"(?<![\w.-]){re.escape(identifier)}(?![\w-])"
    return re.search(pattern, text, flags) is not None


async def backfill(db: aiosqlite.Connection) -> bool:
    """Index the chunks stored before the index existed. Whether it had to.

    A new external-content index starts empty, so every document already there would
    never be found by keyword. Counted, not rebuilt at every start: a rebuild reads the
    whole library. The count comes from `_docsize`, one row per indexed chunk; a
    `count(*)` on the index itself counts the content table and answers "all there".
    """
    async with db.execute("SELECT count(*) FROM rag_chunks") as cur:
        chunks = (await cur.fetchone() or (0,))[0]
    async with db.execute("SELECT count(*) FROM rag_chunks_fts_docsize") as cur:
        indexed = (await cur.fetchone() or (0,))[0]
    if chunks == indexed:
        return False
    await db.execute("INSERT INTO rag_chunks_fts(rag_chunks_fts) VALUES('rebuild')")
    return True


def match_query(question: str) -> str:
    """A question as a safe FTS5 MATCH expression: each term quoted, the terms OR-ed,
    each once and the identifiers' first, so a long question can't push them past the
    cap.

    Quoted, an FTS5 operator is only text; a term is letters and digits, so it can't
    hold a quote to close its own. "" when no term is left, which the caller must read
    as "search nothing": an empty MATCH is a syntax error.
    """
    named = [term for word in identifiers(question) for term in _TERM.findall(word)]
    terms: dict[str, str] = {}
    for term in chain(named, (match.group() for match in _TERM.finditer(question))):
        terms.setdefault(term.casefold(), term)  # the index folds case too
        if len(terms) == _MAX_TERMS:
            break
    return " OR ".join(f'"{term}"' for term in terms.values())
