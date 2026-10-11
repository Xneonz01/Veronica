"""The claim check's table: which sentences of a reply say she did something, and what.

**A table, not a model.** A closed set of first-person action words, one row per kind of
thing a tool can do. It reads words, not meaning, so a paraphrase it has no row for goes
unread, and a row that fires on sentences that aren't claims comes out of the table
rather than growing exceptions. Only her own words are read (`claims_voice.py`).

**First person.** A claim needs its subject: "I", then optionally one of a closed set of
auxiliaries and adverbs ("I've", "I have just", "I went ahead and"), then the verb. So
"you saved it", "it was saved" and "I haven't saved it" are not claims. A promise ("I'll
remember that") is one too, and says so: an attempt that failed keeps a promise, where
a report ("I've saved it") needs the action to have worked.

**Sentences, clauses, parts.** A sentence ends at a mark or a line break, a dash or a
semicolon ends a clause, and a comma a part. A question asks, so its last part is left
out and what comes before still claims ("I've saved that, anything else?"). A promise in
a part with "if" or "unless", or after a part that opens with a condition ("If you tell
me, I'll remember it", "Once you confirm, I'll save it"), is an offer. "Already", "last
week" and "yesterday" point at an earlier turn, which this one can't vouch for.

**Words that mean more than one thing** count only beside what they are about.
- Changing and removing words are about her memory only beside it ("my memory", "your
  preferences"; not "the memory" or "memory usage", a computer's).
- Writing words are about a file only when a file is what they acted on ("I saved the
  file", "I wrote notes.md") or where they put something ("...to notes.md"), never a
  file named elsewhere ("error handling for when the file doesn't exist").
- "Opened" is about an app beside one, and "launched" only beside one she may open ("I
  launched a mobile app" is a CV line).
- "Saved" is about a file when a file is its object or target, and otherwise either.
- "Remembered" and "forgotten" count only in the perfect: "I remembered you like tea" is
  recall; "I've forgotten to mention" and "Sorry, I've forgotten your name" are not
  about her memory.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator
from typing import Literal, NamedTuple

from app.services.memory.normalize import normalize
from app.services.tools.claims_voice import own_lines

Kind = Literal["memory_saved", "memory_changed", "memory_forgotten", "files", "apps"]


class Claim(NamedTuple):
    """The kinds of action that could have done what a sentence says, and whether it
    said so as a promise ("I'll…") rather than a report ("I've…")."""

    kinds: tuple[Kind, ...]
    promise: bool = False


class _Clause(NamedTuple):
    """What a whole clause says about its topic, for each of its parts."""

    memory: bool  # her memory is named
    app: bool  # an app is named, by kind
    named_app: bool  # an app she may open is named
    sorry: bool


_SENTENCES = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+")  # "notes/ideas.md" and 3.5 don't end one
_CLAUSES = re.compile(r"\s*[—–]\s*| - |;\s*")
_TOKEN = re.compile(r"[\w./\\-]+")
_ACKNOWLEDGED = ("ok", "okay", "sure", "done", "got it", "all right", "alright", "understood")

# Extensions that name a document. Script and framework names are left out: "Node.js"
# is a framework as often as a file.
_EXTENSIONS = "md|txt|rtf|doc|docx|pdf|csv|json|yaml|yml|toml|ini|cfg|log|py|html|xml|odt"
_NAMED_FILE = re.compile(rf"[\w.-]*\.(?:{_EXTENSIONS})")
_FILE_WORDS = frozenset({"file", "files", "folder", "folders"})
_DETERMINERS = frozenset({"the", "a", "an", "your", "my", "our", "this", "that", "these", "new"})
_PRONOUNS = frozenset({"it", "that", "this", "them", "these", "those", "everything"})
_TARGETS = frozenset({"to", "into", "in", "as", "under", "inside", "onto"})
# Words that end what a verb acted on: "added error handling [for] when the file…".
_BOUNDARY = frozenset(
    "of for from with that which who whose where when while because so and but or about on"
    " at by using via like than if unless after before since until".split()
)
# What she made is in this reply, not on disk: "I've created a sample config.yaml below".
_IN_REPLY = frozenset({"below", "above", "here", "following"})
_OBJECT_WINDOW = 8

_MEMORY_TOPIC = re.compile(
    r" (?:my|your|that|this) (?:memory|memories)\b"
    r"(?! (?:usage|use|leak|leaks|footprint|limit|limits|card|cards|stick|allocation))"
    r"| (?:committed|saved|stored|added|put)(?: [\w.-]+){0,3} to memory\b"
    r"| (?:my|your) preferences\b| what i (?:remember|know)\b"
)
# "Saved that you prefer tea": a fact about you is a memory, whatever else it could be.
_ABOUT_YOU = re.compile(r"^ that (?:you|your)\b")
_APP_TOPIC = re.compile(r" (?:app|apps|application|program)\b")

_AUX = r"(?: (?:ve|have|just|also|now|actually|gone ahead and|went ahead and))*"
_I = r"(?<! if)(?<! unless)(?<! whether) i"  # "I", not supposed ("if I save it")
_PROMISE = r" i (?:ll|will|m going to|am going to)(?: make sure to| be sure to)?"
_PROMISED = re.compile(r" i (?:ll|will|m going to|am going to|won t|will not) ")
_IF = re.compile(r" (?:if|unless)\b")
_OPENS_CONDITION = re.compile(r"^ (?:if|unless|once|when|whenever|after|as soon as)\b")
_EARLIER = re.compile(r" (?:already|last time|last week|yesterday)\b")
_SORRY = re.compile(r" (?:sorry|apologi[sz]e)\b")
# "Saved" as a favour or an order ("saved you a spot", "saved the best for last"), and
# keeping in mind for what this reply goes on to say ("…for the recipes below").
_NOT_KEPT = re.compile(r" saved you (?:a|an|the|some)\b| for (?:last|the end)\b| below\b| above\b")


def _pattern(past: str, *, perfect: str = "", promised: str = "") -> re.Pattern[str]:
    parts = [rf"{_I}{_AUX} (?:{past})\b"]
    if perfect:  # an auxiliary is required, and "forgotten to" is an idiom
        parts.append(rf"{_I} (?:ve|have)(?: (?:just|also|now|actually))* (?:{perfect})\b(?! to\b)")
    if promised:
        parts.append(rf"{_PROMISE} (?:{promised})\b")
    return re.compile("|".join(parts))


_SAVED = _pattern("saved|stored", perfect="remembered", promised="remember|store")
_SAVED_BY_TOPIC = _pattern("added|committed", promised="add|save|commit")
_KEPT = re.compile(
    rf"{_PROMISE} (?:keep (?:that|it|this) in mind|make a note of (?:that|it|this))\b"
    rf"| i (?:won t|will not) forget\b"
    rf"|{_I}{_AUX} (?:noted|made a note)(?: of)?(?: that)? you\b"
)
_CHANGED_BY_TOPIC = _pattern("updated|corrected|changed", promised="update|correct|change")
_FORGOTTEN = _pattern("erased", perfect="forgotten", promised="forget|erase")
_FORGOTTEN_BY_TOPIC = _pattern("deleted|removed", promised="delete|remove")
# Her report of a memory with no subject: "Noted: you prefer…", "Saved: User takes…",
# "Forgotten." Every memory she saves begins "User", so "Corrected:" and "Updated:"
# count only before it: on their own they introduce an edit ("Updated. Here's the plan").
_BARE_OBJECT = r" (?:that|it|this|you|your|user)\b"
_BARE = re.compile(
    rf"^ (remembered|saved|stored|forgotten)(?:{_BARE_OBJECT}|$)"
    rf"|^ (noted){_BARE_OBJECT}"
    r"|^ (corrected|updated) user\b"
)
_BARE_KIND: dict[str, tuple[Kind, ...]] = {
    "remembered": ("memory_saved",),
    "saved": ("memory_saved", "files"),
    "stored": ("memory_saved",),
    "noted": ("memory_saved",),
    "forgotten": ("memory_forgotten",),
    "corrected": ("memory_changed",),
    "updated": ("memory_changed",),
}
_FILES = _pattern(
    "wrote|written|saved|created|updated|added|appended",
    promised="write|save|create|update|add|append",
)
_OPENED = _pattern("opened", promised="open")
_LAUNCHED = _pattern("launched", promised="launch")


def claimed(text: str, app_names: Collection[str] = ()) -> list[Claim]:
    """Each claim in `text`, in reading order. `app_names` are the apps she may open.

    A line that names her memory and ends in a colon lends it to the list below it
    ("I've made these changes to my memory:" then "- I removed the gym one").
    """
    apps = tuple(words for name in app_names if (words := _words(normalize(name))))
    found: list[Claim] = []
    heading = False
    for line in own_lines(text):
        folded = normalize(line)
        for sentence in _SENTENCES.split(folded):
            found.extend(_sentence(sentence.strip().rstrip("\"'”’)]"), apps, heading))
        if folded.endswith(":"):
            heading = bool(_MEMORY_TOPIC.search(_words(folded)))
    return found


def _words(text: str) -> str:
    return "".join(f" {token.rstrip('.')}" for token in _TOKEN.findall(text))


def _sentence(sentence: str, apps: tuple[str, ...], heading: bool) -> list[Claim]:
    found: list[Claim] = []
    for clause in _CLAUSES.split(sentence) if sentence else []:
        if clause.endswith("?"):
            clause = clause.rsplit(",", 1)[0] if "," in clause else ""
        parts = [_words(part) for part in clause.split(",")]
        whole = "".join(parts)
        about = _Clause(
            memory=heading or bool(_MEMORY_TOPIC.search(whole)),
            app=bool(_APP_TOPIC.search(whole)),
            named_app=any(f"{name} " in f"{whole} " for name in apps),
            sorry=bool(_SORRY.search(whole)),
        )
        for index, words in enumerate(parts):
            if not words or _EARLIER.search(words):
                continue
            after_condition = index > 0 and _OPENS_CONDITION.match(parts[index - 1])
            offered = bool(_IF.search(words) or _OPENS_CONDITION.match(words) or after_condition)
            found.extend(_read(words, about, offered))
    return found


def _read(words: str, about: _Clause, offered: bool) -> list[Claim]:
    def matches(pattern: re.Pattern[str]) -> Iterator[re.Match[str]]:
        return (m for m in pattern.finditer(words) if not (offered and _promised(m)))

    def first(pattern: re.Pattern[str]) -> re.Match[str] | None:
        return next(matches(pattern), None)

    claims: list[Claim] = []
    written = next((m for m in matches(_FILES) if _file_object(words[m.end() :])), None)
    if written:
        claims.append(Claim(("files",), _promised(written)))
    kept = not _NOT_KEPT.search(words)
    saved = next(
        (m for m in matches(_SAVED) if not (" sav" in m[0] and _file_object(words[m.end() :]))),
        None,
    )
    saved = saved or first(_KEPT) or (first(_SAVED_BY_TOPIC) if about.memory else None)
    if saved and kept:
        either = " sav" in saved[0] and not (about.memory or _ABOUT_YOU.match(words[saved.end() :]))
        kinds: tuple[Kind, ...] = ("memory_saved", "files") if either else ("memory_saved",)
        claims.append(Claim(kinds, _promised(saved)))
    changed = first(_CHANGED_BY_TOPIC) if about.memory else None
    if changed:
        claims.append(Claim(("memory_changed",), _promised(changed)))
    forgot = first(_FORGOTTEN) or (first(_FORGOTTEN_BY_TOPIC) if about.memory else None)
    if forgot and not about.sorry:
        claims.append(Claim(("memory_forgotten",), _promised(forgot)))
    plain = _unacknowledged(words)
    bare = _BARE.search(plain)
    if bare and kept and not (saved or changed or forgot):
        word = bare[1] or bare[2] or bare[3]
        on_file = word == "saved" and _file_object(plain[bare.end(1) :])
        claims.append(Claim(("files",) if on_file else _BARE_KIND[word]))
    opened = first(_OPENED) if about.app or about.named_app else None
    launched = opened or (first(_LAUNCHED) if about.named_app else None)
    if launched:
        claims.append(Claim(("apps",), _promised(launched)))
    return claims


def _promised(match: re.Match[str]) -> bool:
    return bool(_PROMISED.match(match[0]))


def _file_object(rest: str) -> bool:
    """Whether what a writing verb acted on is a file: its object ("the file", "a new
    notes folder", "notes.md") or the place it put something ("to notes.md", "into your
    notes file"). "To" before a verb ("to read the file") is not a place."""
    tokens = rest.split()[:_OBJECT_WINDOW]
    start = 1 if len(tokens) > 1 and tokens[0] in _PRONOUNS and tokens[1] in _TARGETS else 0
    thing: list[str] = []
    for token in tokens[start:]:
        if token in _BOUNDARY or token in _TARGETS or token in _IN_REPLY:
            break
        thing.append(token)
    after = tokens[start + len(thing) :]
    if after[:1] and after[0] in _IN_REPLY:
        return False
    if thing and (thing[-1] in _FILE_WORDS or any(_is_file_name(t) for t in thing)):
        return True
    if not after or after[0] not in _TARGETS:
        return False
    place = after[1:]
    lead = 0
    while lead < len(place) and place[lead] in _DETERMINERS:
        lead += 1
    head = []
    for token in place[lead : lead + 4]:
        if token in _BOUNDARY or token in _TARGETS:
            break
        head.append(token)
    if any(_is_file_name(token) for token in head[:2]):
        return True
    return lead > 0 and any(token in _FILE_WORDS for token in head)


def _is_file_name(token: str) -> bool:
    """A document's name, a path ending in one, or a path: a leading separator or two of
    them, with a letter in it ("1/2", "and/or", "12/05/2026" and a link's "//host/…" are
    not paths)."""
    if _NAMED_FILE.fullmatch(token.replace("\\", "/").rsplit("/", 1)[-1]) and not (
        token.startswith("//")
    ):
        return True
    separators = token.count("/") + token.count("\\")
    if not separators or token.startswith("//") or not any(c.isalpha() for c in token):
        return False
    return token[0] in "/\\" or separators >= 2


def _unacknowledged(words: str) -> str:
    """`words` without an opening "Done", "Got it" or "Okay"."""
    for opener in _ACKNOWLEDGED:
        if words.startswith(f" {opener} ") or words == f" {opener}":
            return words[len(opener) + 1 :]
    return words
