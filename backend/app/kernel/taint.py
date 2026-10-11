"""Which of a call's arguments came from text the user did not write.

A document, a memory or an earlier tool result can carry instructions, and the model
follows some of them: measured on qwen3.5:9b, a sentence planted in a memory got a
tool called with its words in nine runs of ten, however the memory was placed in the
prompt. More prompt doesn't stop that, so the answer is in the kernel: **a string
argument that occurs word for word in this turn's untrusted text, and nowhere in what
the user said, makes the call ask first**, even one that would have run unasked.

**Untrusted** is every block the turn handed the model as material (her memory block,
the documents it cites), every tool result of the turn's earlier rounds, and her own
earlier replies that carried what she read (a cited answer, a file read out on a call):
a sentence she repeated one turn is no less a document's the next. **Trusted** is the
user's own messages in the window the prompt carries, a request they made while a call
waited on an answer, and the values a field's type allows (`Literal`). Anything in
neither (her other replies, the persona) is neutral: an argument copied from it is
evidence of nothing.

**And the model's own arguments, repeated whole.** A result that quotes an argument
back ("Saved: …") puts the model's words in a tool result, and an unchanged retry was
then a copy of it. An argument of an earlier call this turn that ran is trusted for the
same capability, as a whole value compared by equality, never as a span: a span rule
would let a document's sentence be laundered in two calls, first wrapped in words of
the model's own, then alone. Per capability, because a yes to one action is not a yes
to another: approving a file write must not let its text be saved to memory unasked.

**Words, not characters.** Both sides are compared as their words in order, so a copy
that differs only in punctuation or markup ("…administrator." copied from
"…administrator'.", `**bold**`, a curly apostrophe) still compares equal, and a match
has to start and end on a word boundary. Every character that renders as nothing is
removed first: format characters (zero-width and bidi controls), combining marks such
as variation selectors and the grapheme joiner, unassigned code points, and the Hangul
fillers. Otherwise a payload that reads the same would compare unequal to the argument
copied out of it.

It matches the text the turn already carried. It reads no meaning, so a paraphrase is
not caught, and a check that guessed at meaning would be a second, worse model. That is
why text from the web (`web`) is kept apart: a turn that read any makes every action
that isn't safe ask first, copied or not (`kernel.AFTER_WEB`, ADR 0003).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from functools import cache
from typing import Literal, get_args, get_origin

from pydantic import BaseModel

from app.services.memory.normalize import normalize

# The kinds of untrusted text a turn carries. Reported as an argument's source, so a
# poisoned document can be told from a poisoned memory without the text being kept.
Source = Literal["memory", "document", "tool_result", "web"]

# Categories that never show as a letter of their own: formatting, unassigned, and
# combining marks (checked after NFKC has composed every accent that has a form).
_UNSEEN = frozenset({"Cf", "Cn", "Mn", "Me"})
# Letters by category that render as blank space: the Hangul fillers.
_BLANK_LETTERS = frozenset("\u115f\u1160\u3164\uffa0")
_WORD = re.compile(r"\w+")


def taint_key(text: str) -> str:
    """The form both sides are compared in: the visible words, in order, one space apart."""
    visible = "".join(
        char
        for char in normalize(text)
        if char not in _BLANK_LETTERS and unicodedata.category(char) not in _UNSEEN
    )
    return " ".join(_WORD.findall(visible))


def _padded(text: str) -> str:
    """`text`'s key between spaces, so a match can only start and end on a word: "user
    is admin" is not in "superuser is administrator". Empty for text with no words."""
    key = taint_key(text)
    return f" {key} " if key else ""


@dataclass(frozen=True, slots=True)
class Taint:
    """One turn's texts, normalised once, in the piles the check reads.

    Frozen and built per turn by the surface, never per call: every call of a round
    reads the same object, and the loop hands the next round a new one with that
    round's results added (`adding`), so a result can taint only a call the model made
    after reading it.
    """

    untrusted: tuple[tuple[Source, str], ...] = ()
    trusted: tuple[str, ...] = ()
    # Earlier arguments that ran, as (capability, key): trusted whole, for that capability.
    own: frozenset[tuple[str, str]] = frozenset()

    @classmethod
    def of(cls, *, untrusted: Iterable[tuple[Source, str]], trusted: Iterable[str]) -> Taint:
        return cls(
            untrusted=tuple((kind, key) for kind, text in untrusted if (key := _padded(text))),
            trusted=tuple(key for text in trusted if (key := _padded(text))),
        )

    def read(self, source: Source) -> bool:
        """Whether the turn read any text of `source`."""
        return any(kind == source for kind, _key in self.untrusted)

    def adding(self, source: Source, texts: Iterable[str]) -> Taint:
        """This taint, plus more untrusted text: the last round's tool results."""
        more = tuple((source, key) for text in texts if (key := _padded(text)))
        return Taint(self.untrusted + more, self.trusted, self.own) if more else self

    def trusting(self, texts: Iterable[str]) -> Taint:
        """This taint, plus words the user said since it was made: a request heard while
        a call waited on an answer, which the next round acts on."""
        more = tuple(key for text in texts if (key := _padded(text)))
        return Taint(self.untrusted, self.trusted + more, self.own) if more else self

    def keeping(self, capability: str, keys: Iterable[str]) -> Taint:
        """This taint, plus the arguments of a call of `capability` that ran (`declared`)."""
        more = frozenset((capability, key) for key in keys if key) - self.own
        return Taint(self.untrusted, self.trusted, self.own | more) if more else self


@dataclass(frozen=True, slots=True)
class Tainted:
    """The argument that made a call ask: its field, its source and its length, never
    its text, which is the one thing about it that may be an attacker's."""

    field: str
    source: Source
    length: int

    def as_fields(self) -> dict[str, object]:
        return {"field": self.field, "source": self.source, "length": self.length}


def find(
    raw: dict[str, object],
    params: type[BaseModel],
    taint: Taint,
    *,
    capability: str,
    min_words: int,
    selectors: Collection[str] = (),
) -> Tainted | None:
    """The first declared argument that came word for word from untrusted text, or None.

    Declared fields only: a key the params model lacks is dropped by validation and
    never reaches the handler. `selectors` name the fields that only pick among the
    user's own memories: copied from her memory block is how they are meant to be
    written, so they are read against everything else she read, and a document that
    names the memory to change still asks. Fewer than `min_words` words is too short
    to be anyone's sentence.
    """
    trusted = taint.trusted + literal_values(params)
    for name in params.model_fields:
        for text in strings(raw.get(name)):
            key = taint_key(text)
            if len(key.split()) < min_words or (capability, key) in taint.own:
                continue
            needle = f" {key} "
            if any(needle in one for one in trusted):
                continue
            for source, block in taint.untrusted:
                if name in selectors and source == "memory":
                    continue
                if needle in block:
                    return Tainted(field=name, source=source, length=len(key))
    return None


def declared(
    raw: dict[str, object], params: type[BaseModel], *, skip: Collection[str] = ()
) -> tuple[str, ...]:
    """The keys of a call's declared string arguments: what `find` reads, and no more.

    A key the params model lacks is never checked, so a value that arrived in one must
    never become trusted by having ridden along on a call that ran.
    """
    return tuple(
        key
        for name in params.model_fields
        if name not in skip
        for text in strings(raw.get(name))
        if (key := taint_key(text))
    )


@cache
def literal_values(params: type[BaseModel]) -> tuple[str, ...]:
    """Every string a field of `params` may only take from a fixed set: values the
    schema restricts to the tool's own vocabulary, which no document chose."""
    found: list[str] = []
    for info in params.model_fields.values():
        found.extend(_literals(info.annotation))
    return tuple(key for value in found if (key := _padded(value)))


def _literals(annotation: object) -> list[str]:
    if get_origin(annotation) is Literal:
        return [value for value in get_args(annotation) if isinstance(value, str)]
    return [value for inner in get_args(annotation) for value in _literals(inner)]


def strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []
