"""What the learning extractor is asked, and how its answer is read and filed.

Pure functions, kept apart from the controller that stores and announces, so the
model evaluations in `backend/eval` measure exactly the rules the product runs. The
word rules that find a fact with nothing new in it are in `fact_words`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, NamedTuple

from pydantic import ValidationError

from app.services.memory.address import ADDRESS_KEY, classify_directive
from app.services.memory.fact_words import (
    copy_of,
    first_word,
    from_this_part,
    singular,
    word_set,
)
from app.services.memory.models import ExtractedFact, Extraction, LearningMode, MemoryKind

# The instruction's examples, which a model can hand back as if the user had said them
# (`unprompted`). Built from words a person seldom says together, so a real fact rarely
# comes out as one: the preference was "User prefers short answers", which a model also
# wrote for "keep it brief", a preference someone had really stated.
EXAMPLE_FACT = "User is restoring a wooden sailing boat"
EXAMPLE_PREFERENCE = "User prefers crossword puzzles printed on paper"
EXAMPLES = (EXAMPLE_FACT, EXAMPLE_PREFERENCE)

_INSTRUCTION = (
    "From the conversation, extract only DURABLE facts about the USER that stay true "
    "beyond this one conversation — preferences, projects, identity, relationships, "
    'skills. Output ONLY JSON shaped {"profile": {}, "facts": [{"content": "...", '
    '"confidence": 0.0, "kind": "fact"}]}. `profile` holds the user\'s name or identity '
    "(key->value, such as name), and only what the user states; otherwise leave it "
    "empty. Each fact is third person, with a 0..1 "
    "confidence (how sure you are the user actually stated it) and kind one of "
    "fact|preference. "
    "Do NOT store: how the conversation is going, the user's current mood or state, what "
    "is being discussed right now, statements about you (the assistant), small talk, or "
    "anything only true within this one conversation. "
    f'Good: {{"content": "{EXAMPLE_FACT}", "confidence": 0.9, '
    f'"kind": "fact"}} and {{"content": "{EXAMPLE_PREFERENCE}", "confidence": 0.95, '
    '"kind": "preference"}. '
    'Bad (do NOT emit): "the user is doing great", "the user is asking what to do next", '
    '"the user said thanks". '
    "If nothing durable was shared, return an empty facts list. Never include secrets, "
    "passwords, or credentials. No prose, JSON only."
)

# A part read after others, worded as measured (bug 40). The model restates the earlier
# turns all the same; `fact_words.from_this_part` drops what it took from them.
_AFTER_EARLIER = (
    "Earlier in this conversation, already read: context only, to know who and what the "
    "new part means. Extract nothing from it.\n{earlier}\n\nThe new part, to extract "
    "from:\n{transcript}"
)
_SPEAKERS = {"user": "User", "assistant": "Veronica"}
# Words the examples share with any fact or summary, which tell none of them apart.
_COMMON = frozenset({"user", "is", "a", "an", "the", "we", "were", "prefers", "on", "to"})
# How people give their own name. "My sister Morgan" says a name, but not theirs: a
# name slot is kept only when the user's words introduce it as their own.
_INTRODUCED = re.compile(
    r"\b(?:my name is|my name's|name's|i am|i'm|call me|it's|it is|this is|i go by)\s+([^\W\d_]+)",
    re.IGNORECASE,
)


def session_transcript(history: Sequence[Mapping[str, Any]]) -> str:
    """A session as the extractor reads it: a `User:` or `Veronica:` line per message.

    Tool rows and bare tool calls are left out. What a file or a recalled memory says
    isn't something the user told her, and once it reads as part of the conversation
    the extractor can't tell the difference.
    """
    return "\n".join(
        f"{_SPEAKERS[message['role']]}: {message['content']}"
        for message in history
        if message.get("role") in _SPEAKERS and message.get("content")
    )


def extraction_messages(transcript: str, earlier: str = "") -> list[dict[str, Any]]:
    """The extraction request exactly as learning sends it. `earlier` is the transcript of
    the last turns read before this part, if any: context, so that "she" in the part has
    someone to be (bug 40), with facts asked of the part alone."""
    if earlier:
        transcript = _AFTER_EARLIER.format(earlier=earlier, transcript=transcript)
    return [
        {"role": "system", "content": _INSTRUCTION},
        {"role": "user", "content": transcript},
    ]


def parse_extraction(reply: str) -> Extraction:
    """The model's reply, read one fact at a time. Raises when it holds no JSON, or its
    facts aren't a list: that is a failed extraction, not an empty one.

    A fact the product can't read (a kind it doesn't know, no content) is dropped by
    itself and counted in `malformed`. Validated as a whole, one took the reply with
    it, and everything learned in that session. A bare list is read as the facts:
    asked for nothing, the model sometimes answers `[]`.
    """
    data = _json_value(reply)
    if isinstance(data, list):
        data = {"facts": data}
    if not isinstance(data, dict):
        raise ValueError("the extraction holds no JSON object")
    facts = data.get("facts") or []
    if not isinstance(facts, list):
        raise ValueError("the extraction's facts are not a list")
    profile = data.get("profile")
    extraction = Extraction(profile=profile if isinstance(profile, dict) else {})
    for item in facts:
        try:
            extraction.facts.append(ExtractedFact.model_validate(item))
        except ValidationError:
            extraction.malformed += 1
    return extraction


def kept_profile(
    profile: Mapping[str, str], history: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """The profile entries a session supports: values the user said themselves.

    Asked for a name it wasn't given, an extractor fills one in anyway: "User",
    "Unknown", an empty string, or her own name from her greeting. A value is kept
    only when every word of it is in the user's own messages, which drops all of
    those without a list of placeholders to keep up to date. A name must also be
    one the user gave as theirs: told "my sister Morgan lives in Leeds", both
    measured models have filed Morgan as the user's name. The form-of-address slot
    is exempt: its values are an enum the user never says word for word, and
    `coerce_address` validates them.
    """
    spoken = " ".join(str(m.get("content") or "") for m in history if m.get("role") == "user")
    said = word_set(spoken)
    introduced = {match.casefold() for match in _INTRODUCED.findall(spoken)}
    kept: dict[str, str] = {}
    for key, value in profile.items():
        words = word_set(value)
        if key == ADDRESS_KEY:
            kept[key] = value
        elif words and words <= said and ("name" not in key or first_word(value) in introduced):
            kept[key] = value
    return kept


class Sifted(NamedTuple):
    """What is left of the facts an extraction proposed, and how many each rule took."""

    facts: list[ExtractedFact]
    examples: int  # the instruction's examples handed back
    copies: int  # copies of what the person asked her to keep
    earlier: int  # restated from the turns read before the part, or learned already


def sift(
    facts: Sequence[ExtractedFact],
    history: Sequence[Mapping[str, Any]],
    kept: Sequence[str],
    *,
    earlier: Sequence[Mapping[str, Any]] = (),
    learned: Sequence[str] = (),
) -> Sifted:
    """The facts that say something new, in the order the rules run: not an example of
    the instruction's (`unprompted`), and not a copy of one of `kept`, what the person
    asked her to keep (`copy_of`). When `history` was read after `earlier`, the turns
    before it, also not restated from those (`from_this_part`), nor a copy of what the
    user said in them or of `learned`, what learning filed lately: a restatement that
    shares a common word with the part ("hard work") gets past the first."""
    examples = unprompted(facts, history)
    fresh = [fact for fact in examples if not copy_of(fact.content, kept)]
    anew = fresh
    if earlier:
        said = [str(m.get("content") or "") for m in earlier if m.get("role") == "user"]
        anew = from_this_part(fresh, history, earlier)
        anew = [fact for fact in anew if not copy_of(fact.content, [*learned, *said])]
    return Sifted(
        anew, len(facts) - len(examples), len(examples) - len(fresh), len(fresh) - len(anew)
    )


def unprompted(
    facts: Sequence[ExtractedFact], history: Sequence[Mapping[str, Any]]
) -> list[ExtractedFact]:
    """The facts that aren't the instruction's examples handed back (`copies_example`)."""
    said = said_words(history)
    return [fact for fact in facts if not copies_example(fact.content, said, EXAMPLES)]


def said_words(history: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """The telling words of what the user said: only their own turns, not hers."""
    spoken = " ".join(str(m.get("content") or "") for m in history if m.get("role") == "user")
    return _telling(spoken)


def copies_example(text: str, said: frozenset[str], examples: Sequence[str]) -> bool:
    """Whether `text` is one of `examples` over again, word for word, when the user said
    at most half of that example's telling words: then it came from the prompt.

    Only a whole copy: a fact that adds to an example, or leaves part of it out, is
    kept, so a real fact is never dropped for resembling one. A copy the model reworded
    gets through, and shows on the Memory page like any other memory.
    """
    words = _telling(text)
    for example in examples:
        telling = _telling(example)
        if words == telling and 2 * len(telling & said) <= len(telling):
            return True
    return False


def _telling(text: str) -> frozenset[str]:
    """The words that tell one sentence from another, a plural read as its singular."""
    return frozenset(singular(word) for word in word_set(text)) - _COMMON


class Filing(StrEnum):
    """What becomes of one extracted fact, before the sensitivity filter has its say."""

    address = "address"  # a form-of-address request: the Tier-1 slot, not a fact
    drop = "drop"  # not durable (an episodic note); the session summary covers it
    store = "store"  # saved active at once: proactive mode and confident enough
    queue = "queue"  # waits in the review queue


def filing_for(
    content: str, confidence: float, kind: MemoryKind, mode: LearningMode, threshold: float
) -> Filing:
    """The filing rule on its own, so it can be tested and measured without a store."""
    if classify_directive(content) is not None:
        return Filing.address
    if kind not in (MemoryKind.fact, MemoryKind.preference):
        return Filing.drop
    if mode is LearningMode.proactive and confidence >= threshold:
        return Filing.store
    return Filing.queue


def _json_value(text: str) -> object:
    """The JSON in a model reply: all of it, or its outermost object, or its outermost
    list (stray prose and code fences around it are tolerated). Raises when none is."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opening, closing in (("{", "}"), ("[", "]")):
        start, end = text.find(opening), text.rfind(closing)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                continue
    raise ValueError("the extraction holds no JSON")
