"""The words learning compares, and the two rules built on them that drop a learned fact
for saying nothing new.

The extractor restates. Told what the person asked her to keep, it files that again in
other words (`copy_of`, bug 39); shown the turns read before a part, it files them again
beside what the part said (`from_this_part` and `copy_of`, bug 40). These are word
rules, not a request to the model: asked not to, it restated anyway (measured for each).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.memory.models import ExtractedFact

_WORD = re.compile(r"[^\W_]+")
# An "-es" after these is the ending, not the word: "teaches" is "teach", not "teache".
_SIBILANT_ES = re.compile(r"(?:ch|sh|x|z|ss)es$")


def word_set(text: str) -> set[str]:
    """The words of `text`, case and width folded."""
    return set(_WORD.findall(unicodedata.normalize("NFKC", text).casefold()))


def first_word(text: str) -> str:
    """The first word of `text`, folded like `word_set`, or "" when it has none."""
    found = _WORD.findall(unicodedata.normalize("NFKC", text).casefold())
    return found[0] if found else ""


def singular(word: str) -> str:
    """A plural, or a verb's "he" form, read as its stem: "cats" is "cat", and "teaches"
    is "teach", as a fact written of the user ("User teaches maths") meets their own
    words ("I teach maths")."""
    if len(word) > 4 and _SIBILANT_ES.search(word):
        return word[:-2]
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def copy_of(content: str, kept: Sequence[str]) -> bool:
    """Whether a learned fact only repeats, in other words, one of `kept`: what the person
    asked her to keep (bug 39: told "these days I water them in the evening", she saved
    it with her tool, and learning filed "User waters their plants in the evening" too).

    A copy says nothing that memory doesn't: every word of it that tells facts apart is
    in the memory. Its negation must match, so "doesn't eat meat" never covers "eats
    meat"; and a fact about the user is never covered by a memory about someone else, so
    "my sister is a nurse" doesn't cover "User is a nurse", while "Daughter Morgan is
    seven" does cover "Morgan is seven". Measured on qwen3.5:9b's filings over three
    runs: every copy caught, 24 of 24 in the six conversations it was designed on and
    12 of 12 in four held out, and no fact said anew dropped.
    """
    words = _fact_words(content)
    if not words:
        return False
    negated, others = _negations(content), _others(content)
    about_user = "user" in word_set(content)
    for memory in kept:
        if not words <= _fact_words(memory) or _negations(memory) != negated:
            continue
        theirs = _others(memory)
        if others <= theirs and (others == theirs or not about_user):
            return True
    return False


def from_this_part(
    facts: Sequence[ExtractedFact],
    history: Sequence[Mapping[str, Any]],
    earlier: Sequence[Mapping[str, Any]],
) -> list[ExtractedFact]:
    """The facts that don't only restate `earlier`, the turns shown as context before
    `history`, the part being learned (bug 40).

    Asked to extract nothing from those turns, the model files them again beside what
    the part says ("User has a sister named Morgan" beside "Morgan is vegetarian"), and
    they were learned when they were read. A restatement shares a word that tells facts
    apart with them and none with what the user said in the part: that is what drops
    it. What the part says shares a word with the part, even about someone named only
    before it, and a new fact in words of the model's own shares none with either.
    Her words in the part don't count, so a fact the user gave with a bare "yes" to
    her question goes with the restatements.
    """
    said, before = _spoken(history, users_only=True), _spoken(earlier, users_only=False)
    return [
        fact for fact in facts if (words := _fact_words(fact.content)) & said or not words & before
    ]


# What turns a claim around, and who else it can be about. "Doesn't" splits into "doesn"
# and "t": the "t" (and "cannot") read as "not", the stem as filler.
_NEGATIONS = frozenset({"not", "no", "never", "nor", "none", "without"})
_AS_NOT = {"t": "not", "cannot": "not"}
_OTHERS = frozenset(
    "mother mum mom father dad parent sister brother son daughter child children "
    "kid baby wife husband partner boyfriend girlfriend fiance fiancee friend colleague "
    "boss neighbour neighbor flatmate roommate grandmother grandma grandfather grandpa "
    "aunt uncle cousin niece nephew family dog cat pet".split()
)
_FILLER = frozenset(
    "a an the and or but of to in on at by for with from into as is are was were be been "
    "am do does did have has had can i my me you your she her he his it its they their "
    "them this that these those who which what so very also just now user named called "
    "doesn don didn isn aren wasn weren hasn haven hadn won wouldn couldn shouldn "
    "mustn".split()
)


def _spoken(history: Sequence[Mapping[str, Any]], *, users_only: bool) -> frozenset[str]:
    """The telling words said in `history`: the user's turns, or everyone's."""
    return _fact_words(
        " ".join(
            str(m.get("content") or "")
            for m in history
            if not users_only or m.get("role") == "user"
        )
    )


def _fact_words(text: str) -> frozenset[str]:
    """The words that tell one fact from another: no filler, no one-letter pieces, and
    no negation (`_negations` holds those), a plural read as its singular."""
    return frozenset(
        singular(word)
        for word in word_set(text)
        if len(word) > 1 and word not in _FILLER and _AS_NOT.get(word, word) not in _NEGATIONS
    )


def _negations(text: str) -> frozenset[str]:
    """The words that turn a claim around."""
    return frozenset({_AS_NOT.get(word, word) for word in word_set(text)} & _NEGATIONS)


def _others(text: str) -> frozenset[str]:
    """The words that put a claim on someone other than the user."""
    return frozenset({singular(word) for word in word_set(text)} & _OTHERS)
