"""Expression tags: the sounds Chatterbox Turbo performs from a tag in the text.

Turbo's tokenizer has nineteen bracketed tags (`added_tokens.json` in its folder). A tag
is performed, not read out: `[laugh]` is a laugh. Only the few in `SPOKEN_TAGS` are
offered to her. Of the rest, some garbled the line around them in a listening test, and
`[dramatic]` and `[angry]` each once spoke words from the reference clip instead
(ARCHITECTURE.md, "Her voice's sounds").

A model also writes stage directions of its own, untold: "*laughs*", "(Sighs.)",
"[smiles]", "*she laughs softly*". Read aloud, the first was spoken as the word
"laughs". So everything that reads as a tag or a stage direction is taken out of what
is shown and remembered, and what reaches the voice is only an allowed tag, in Turbo's
spelling, or nothing.

The text arrives a few characters at a time, so `ExpressionFilter` holds back a piece
that might be a tag ("[la") until it is closed or clearly isn't one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.adapters.tts.text import split_sentences

# Offered to her on a call (`persona/defaults.py`), chosen in the 0.36.0 listening test.
SPOKEN_TAGS: tuple[str, ...] = ("laugh", "chuckle", "sigh")

# A model's own words for a sound, to the tag that makes it. Only sounds she may make
# are here: a direction for anything else ("[smiles]", "*nods*") is dropped.
_ALIASES = {
    "laugh": "laugh", "laughs": "laugh", "laughing": "laugh", "laughter": "laugh",
    "chuckle": "chuckle", "chuckles": "chuckle", "chuckling": "chuckle",
    "giggle": "chuckle", "giggles": "chuckle", "giggling": "chuckle",
    "sigh": "sigh", "sighs": "sigh", "sighing": "sigh",
    "gasp": "gasp", "gasps": "gasp", "gasping": "gasp",
}  # fmt: skip
# First words of a direction in asterisks or parentheses: those are only a direction
# when they start with one of these, since "*really*" is emphasis and "(see above)" or
# "(sadly, no)" an aside. In square brackets any short run of words is one: a call has
# no citations.
_DIRECTION_WORDS = frozenset(
    {
        *_ALIASES, "smiles", "smiling", "grins", "grinning", "nods", "nodding", "pauses",
        "winks", "shrugs", "coughs", "cough", "clears", "sniffs", "groans", "whispers",
        "whispering",
    }
)  # fmt: skip
# "*she laughs*": the one doing it, before what is done.
_SUBJECTS = frozenset({"she", "she's", "he", "he's", "i", "i'm", "veronica"})
# Words of sympathy. A laugh in the same sentence as one of them reads as mockery: told
# when to laugh, qwen3.5:9b still opened "[laugh] That's rough. You're safe now" to a
# panic attack. Over 0.36.0's wording trials, taking the laugh out of such a sentence
# caught 12 of the 19 laughs at bad news, and cost 6 of the 68 at jokes.
_SYMPATHY = re.compile(
    r"\b(sorry|rough|worst|terrible|awful|oh no|scared|scary|frightening|safe|heavy|loss"
    r"|sad|frustrat\w*|pain\w*|tough|hard time|condolences|upsetting|unsettling"
    r"|stressful|disaster|bummer|shame|horrible)\b",
    re.IGNORECASE,
)
_LAUGHS = re.compile(r"\[(?:laugh|chuckle)\] ?")
_CLOSERS = {"[": "]", "*": "*", "(": ")"}
# The longest piece held back waiting for its closer; past this it is ordinary text,
# whether it arrives whole or a token at a time.
_MAX_PIECE = 40
# What a direction is made of: words and the punctuation a model puts in one ("Sighs.",
# "laughs, shaking head"). A digit or any other symbol makes it ordinary text: "[1]".
_DIRECTION = re.compile(r"[a-z' ,.!?;:-]+")
_WORD = re.compile(r"[a-z]+(?:['-][a-z]+)*")


def classify(piece: str) -> str | None:
    """What a bracketed `piece` ("[laugh]", "*Sighs.*") is: a tag's name, "" for a
    direction to drop, or None when it is ordinary text."""
    if len(piece) > _MAX_PIECE:
        return None
    opener, inner = piece[0], piece[1:-1].strip().lower().replace("\u2019", "'")
    words = _WORD.findall(inner)
    if not words or len(words) > 6 or not _DIRECTION.fullmatch(inner):
        return None
    if words[0] in _SUBJECTS and len(words) > 1:
        words = words[1:]
    if opener != "[" and words[0] not in _DIRECTION_WORDS:
        return None
    return _ALIASES.get(words[0], "")


@dataclass(slots=True)
class Split:
    """One piece of a reply as each reader gets it."""

    shown: str  # the transcript, history and learning: never a tag
    spoken: str  # the voice: an allowed tag kept, when expression is on


class ExpressionFilter:
    """Split streamed reply text into what is shown and what is spoken."""

    def __init__(self, *, expressive: bool) -> None:
        self._expressive = expressive
        self._held = ""
        # Spaces at the end of what is shown wait for the next word: a piece taken out
        # after them would leave two ("Well,  okay"), or one before a full stop.
        self._space = ""
        self._started = False
        self._swallow = False  # a piece was taken out: the spaces after it go

    def feed(self, text: str) -> Split:
        self._held += text
        return self._drain(final=False)

    def end(self) -> Split:
        """The text is over: release what was held as it stands. A space it ended on is
        dropped: the next model call's text brings its own (`workers.respond`)."""
        split = self._drain(final=True)
        self._space = ""
        return split

    def _drain(self, *, final: bool) -> Split:
        shown: list[str] = []
        spoken: list[str] = []
        text = self._held
        while text:
            start = min((i for i in (text.find(c) for c in _CLOSERS) if i >= 0), default=-1)
            if start < 0:
                self._plain(text, shown, spoken)
                text = ""
                break
            self._plain(text[:start], shown, spoken)
            text = text[start:]
            close = text.find(_CLOSERS[text[0]], 1)
            if close < 0 and not final and len(text) < _MAX_PIECE:
                break  # may still close: hold it
            tag = classify(text[: close + 1]) if close > 0 else None
            if tag is None:
                # Only the opener is ordinary text: a tag may sit inside what follows,
                # "(well, [laugh] fine)".
                self._plain(text[0], shown, spoken)
                text = text[1:]
                continue
            text = text[close + 1 :]
            if self._expressive and tag in SPOKEN_TAGS:
                spoken.append(f"[{tag}]")
            self._swallow = bool(self._space) or not self._started
        self._held = text
        return Split("".join(shown), "".join(spoken))

    def _plain(self, text: str, shown: list[str], spoken: list[str]) -> None:
        spoken.append(text)  # the voice keeps its spacing: "[laugh] Okay", not "[laugh]Okay"
        if self._swallow:
            # Nothing before the piece: its punctuation goes too ("[laugh]. Okay").
            text = text.lstrip(" ") if self._started else text.lstrip(" ,.;:!?")
            if not text:
                return
            self._swallow = False
            if text[0] in ",.;:!?":
                self._space = ""
        text = self._space + text
        body = text.rstrip(" ")
        self._space = text[len(body) :]
        if body:
            shown.append(body)
            self._started = True


def holds_laugh(text: str) -> bool:
    """Whether `text` has a laugh or a chuckle in it: such a clause waits for the rest of
    its sentence before it is said (`chunker.py`), so `considerate` reads it whole."""
    return _LAUGHS.search(text) is not None


def considerate(sentence: str, before: str = "") -> str:
    """`sentence`, as her voice will say it: no laugh if it sympathises, and no sigh
    with nothing to say.

    A tag at the end of a reply ("Oh no, that's awful. [laugh]") is a chunk of its own;
    with no words of its own, it goes with the sentence before it, the last in `before`.
    Alone, Turbo laughed for a laugh, and for a sigh said "Yeah." in 2 of 4 takes.
    """
    words = without_tags(sentence)
    said = words or next(iter(split_sentences(without_tags(before))[-1:]), "")
    if _SYMPATHY.search(said):
        sentence = _LAUGHS.sub("", sentence)
    return "" if not words and sentence.strip() == "[sigh]" else sentence


def without_tags(text: str) -> str:
    """`text` as shown: tags and stage directions taken out."""
    tidy = ExpressionFilter(expressive=False)
    return (tidy.feed(text).shown + tidy.end().shown).strip()
