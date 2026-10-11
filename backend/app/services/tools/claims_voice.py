"""Which words of a reply are hers: what the claim check reads, and what it leaves out.

A reply often carries text in someone else's voice: a draft she writes for you says "I"
in your voice, and so do quotes, quoted mail, code and a CV line. Only her own words
can claim something she did, so the rest is left out before `claims_table` reads them.

- **Code, block quotes and quotes** in double, single or curly quotes. An apostrophe
  inside a word ("I've") doesn't close a single quote.
- **Everything after a line that introduces another's text**: one that offers a draft,
  an email, a message, a letter, a post or a version ("Here's a draft you can send",
  "Here's the new version:"), whatever it ends with; a heading naming one ("**Draft**");
  or a letter's salutation on its own line ("Hi Sam," "To whom it may concern,"). Her
  own greeting ("Hi Rowan!") and a list of what she did ("Here's what I did:") are
  still hers.
- **The rest of a line after a lead-in** such as "you could say" or "try this".
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from app.services.memory.normalize import normalize

_FENCED = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# Bounded, so a line with many unclosed quotes costs a fixed amount per quote.
_QUOTED = re.compile(
    r"\"[^\"\n]{0,600}\"|“[^“”\n]{0,600}”"
    r"|(?<!\w)'(?:[^'\n]|'(?=\w)){1,600}?'(?!\w)"
    r"|‘(?:[^‘’\n]|’(?=\w)){1,600}?’(?!\w)"
)
# A letter's opening line: a greeting and up to three words of a name, ending in a comma,
# a colon or nothing. One that ends in "!" is her greeting to you.
_SALUTATION = re.compile(
    r"(?:(?:hi|hello|hey|dear|good (?:morning|afternoon|evening)|morning|afternoon|evening)"
    r"(?: [\w.'’-]+){0,3}|to whom it may concern)[,:]?"
)
_OTHER_TEXT = (
    r"(?:draft|e-?mail|message|reply|response|letter|post|version|template|note|bio|cv"
    r"|resume|résumé|cover letter|caption|tweet|announcement)s?"
)
_INTRODUCES = re.compile(rf"\b(?:here(?:'s|’s| is| are)|below is) .*\b{_OTHER_TEXT}\b")
_HEADING = re.compile(rf"^(?:#+ |\*\*|__).*\b{_OTHER_TEXT}\b")
_LEAD_IN = re.compile(
    r"\b(?:try this|try something like|something like"
    r"|you (?:could|can|might|may) (?:say|write|put|use|send|try))\b",
    re.I,
)


def own_lines(text: str) -> Iterator[str]:
    """The lines of `text` in her own voice, up to one that introduces another's text."""
    for raw in _INLINE_CODE.sub(" ", _FENCED.sub(" ", text)).splitlines():
        line = raw.strip()
        if line.startswith(">"):
            continue
        folded = normalize(line)
        if _SALUTATION.fullmatch(folded):
            return
        yield _LEAD_IN.split(_QUOTED.sub(" ", line), maxsplit=1)[0]
        if _INTRODUCES.search(folded) or _HEADING.search(folded):
            return
