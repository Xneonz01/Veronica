"""The sensitivity guard: content Veronica must never store.

A pure predicate over text with no dependency on the store, the bus or the embedder,
so modules outside MemoryService (the RAG indexer, the persona editor, the audit log)
use it too.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from app.core.text_safety import searchable


class SensitiveContentError(ValueError):
    """Raised when a write is refused because it looks like a secret/credential."""


# A value separator: config-style (=/:), spoken ("is"/"are"), reported ("of", "was",
# "set to": the memory extractor restates what it hears, and told "my wifi password is
# hunter two" a model wrote "User has a password of Hunter2"), or an arrow. A slash
# counts only when spaced ("admin / hunter2"); a bare one is ordinary prose, as in
# "refactoring the login/logout flow".
_SEP = r"(?:is|are|was|were|of|set to|[:=]|->|—|/(?=\s))"
# Unambiguous secret nouns, which justify blocking on a spoken "is" alone. They include
# the everyday access codes people say out loud: "the safe code is 4455" must never
# become a durable, recallable memory. "API key" is spoken with a space.
_SECRET_WORDS = (
    r"passwords?|passwd|pwd|passphrases?|pass ?codes?|api[ _-]?keys?"
    r"|otps?|one[- ]time codes?|2fa backup codes?"
    r"|(?:gate|door|garage|keypad|alarm|safe|lock) ?codes?|pin numbers?"
)
# Ambiguous in ordinary prose ("the login is the first screen", "our credentials are
# managed by the keychain", "the secret is to preheat the oven"), so these need a
# config-style separator rather than "is"/"are".
_AMBIGUOUS_WORDS = r"secrets?|logins?|credentials?"
# What follows a separator is a value only if it isn't a determiner or a pronoun: a
# credential never is one, and "the password is the same as before" or "User changed the
# password of their router" are memories to keep.
_NOT_A_VALUE = r"(?:the|a|an|my|our|your|their|his|her|its|this|that|it)\b"
_VALUE = rf"(?!{_NOT_A_VALUE})\S{{3}}"
# For `_has_credential_value`: a secret noun, and what a credential looks like.
_SECRET_NOUN = re.compile(rf"\b({_SECRET_WORDS})\b", re.I)
_DETERMINERS = frozenset({"the", "a", "an", "my", "our", "your", "their", "his", "her", "its"})
# The words that can sit between a secret noun and its value in a paraphrase ("a
# password named Hunter2", "passcode, which is 8891"). Any other word means the noun is
# about something else: "User's password manager is 1Password".
_CONNECTIVES = frozenset(
    "named called is are was were of which set to as now still currently be being reads".split()
)
# Letters and digits mixed ("Hunter2", "sk-liveabc1"), or a bare digit run ("4455"):
# shapes ordinary English words don't have.
_VALUE_SHAPED = re.compile(r"^(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{3,}$|^#?\d{3,}$")
_VALUE_TOKENS = 4  # how many words after the noun to look at

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
_LINE_BREAK = re.compile(r"[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")
# A line of nothing but a token's characters, sixteen or more, padding only at its end
# and no path or option at its start: a value wrapped onto the lines after its own.
# "DEBUG=true" and "./Install.ps1" aren't one.
_WRAPPED = re.compile(r"[A-Za-z0-9+_][A-Za-z0-9+/_.-]{15,}={0,2}")
# The end of a line a value was cut at: sixteen of a token's characters.
_CUT = re.compile(r"[A-Za-z0-9+/_.-]{16}={0,2}\Z")
_B64 = "[A-Za-z0-9_-]"

# Each pattern is looked for again from just past where each of its finds starts
# (`_finds`), so none runs on past what it needs, or the same text would be read once
# per find: a value is three characters, a number three digits, a key its first twenty,
# and `*+` takes spaces without giving any back. Where a secret is blanked doesn't
# depend on it: a find's line goes whole.
_SENSITIVE = (
    # keyword followed by a value, config-style (=/:) or spoken ("password is X").
    re.compile(rf"\b({_SECRET_WORDS})\b\s*+{_SEP}\s*+{_VALUE}", re.I),
    # No "->" here: arrows are flow notation ("Step 1: login -> Step 2: dashboard").
    re.compile(rf"\b({_AMBIGUOUS_WORDS})\b\s*+(?:[:=]|/(?=\s))\s*+{_VALUE}", re.I),
    # "the alarm pin is 9021" — `pin` alone is ambiguous, so require a digit value, and
    # exclude the codes that are not secrets (zip/area/post/dialling/country/error).
    # "of", "was" and "to" are the extractor's rewordings: "User has a bank PIN of
    # 4821", "User set the garage code to 1122". "To" is safe here only because a digit
    # value is required: "the code to unlock the door" is left alone.
    re.compile(
        r"(?<!\bzip )(?<!\barea )(?<!\bpost )(?<!\bdial )(?<!\bcountry )(?<!\berror )"
        r"\b(pin|code)\b\s*+(?:is|are|was|were|of|to|[:=])\s*+#?\d{3}",
        re.I,
    ),
    # "User's PIN 4821": four or more digits straight after `pin`. Fewer are left alone:
    # "the sensor is on pin 25" is wiring, not a secret.
    re.compile(r"\bpin\b\s*+#?\d{4,}+\b", re.I),
    # "Gate keypad code 4455": an access noun, then "code" and a number. Only
    # punctuation or spaces may sit between "code" and its digits, so documentation
    # like "the fire alarm complies with code BS 5839" isn't taken for an access code.
    re.compile(
        r"\b(gate|door|garage|keypad|alarm|safe|lock)\b[^.\n]{0,20}\bcode\b[^\w\n]{0,4}\d{3}",
        re.I,
    ),
    re.compile(r"\btokens?\b\s*+[:=]\s*+\S", re.I),  # "token" only with :/= (more ambiguous)
    # Creds embedded in a stream URL. The user's part runs to the first colon, an @ in
    # it allowed: let it hold colons too and "rtsp://" and 32,000 of them took 3 s.
    re.compile(r"rtsp://[^\s/:]*+:[^\s/@]*+@", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20}"),  # GitHub token
    re.compile(r"\bsk-[A-Za-z0-9_-]{20}"),  # OpenAI-style key
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10}"),  # Slack token
    # A JWT. Its first part stops at a `-eyJ`, where the next look starts: from every
    # `-eyJ` in one long run, each look read the rest of it.
    re.compile(rf"\beyJ(?:[A-Za-z0-9_]|-(?!eyJ)){{8,}}+\.{_B64}{{8,}}+\.{_B64}{{8}}"),
    # (Card numbers are checked separately, by Luhn — see `_has_card_number`.)
    _PRIVATE_KEY,
    # Arabic is supported throughout, so the guard covers it too:
    # "كلمة السر/المرور" (password), "الرقم السري" (PIN), "رمز" (code).
    # A value of three or more, or a short word and then one: spaces split two ways took
    # 8 s on 32,000 of them.
    re.compile(
        r"(كلمة\s*(?:السر|المرور)|الرقم\s*السري|رمز\s*(?:الدخول|الأمان))"
        r"\s*+(?:\S{3}|\S{1,2}\s++\S{3})"
    ),
)


_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _credential_values(text: str) -> Iterator[tuple[int, int]]:
    """Where a secret noun is followed, across only connecting words, by something
    shaped like a credential: the value's span. The separators above chase connectives,
    and a paraphrasing extractor keeps finding new ones ("a Wi-Fi password named
    Hunter2"); the value's shape doesn't change. A determiner before it makes it a name
    ("the gate code for the B12 allotment"), and any other word ends the look:
    "password manager is 1Password".
    """
    for match in _SECRET_NOUN.finditer(text):
        tail = list(re.finditer(r"[\w#-]+", text[match.end() : match.end() + 48]))
        for index, token in enumerate(tail[:_VALUE_TOKENS]):
            word = token.group()
            if _VALUE_SHAPED.match(word) and (
                index == 0 or tail[index - 1].group().lower() not in _DETERMINERS
            ):
                yield match.end() + token.start(), match.end() + token.end()
                break
            if word.lower() not in _CONNECTIVES:
                break


def _has_credential_value(text: str) -> bool:
    return next(_credential_values(text), None) is not None


def is_secret_name(name: str) -> bool:
    """Whether a field's name says it holds a secret ("password", "api_key", "client
    secret"). The audit log redacts such a field whatever it holds: a passphrase can
    begin with "the"."""
    words = re.sub(r"[_\-.]+", " ", name).strip()
    secret = rf"(?:{_SECRET_WORDS}|{_AMBIGUOUS_WORDS}|tokens?|pin)"
    return bool(re.fullmatch(rf"(?:\w+ )*{secret}", words, re.I))


def _card_numbers(text: str) -> Iterator[tuple[int, int]]:
    """Digit runs that actually pass the Luhn checksum.

    Length alone is not enough: 13-16 digit runs are also epoch-ms timestamps, order
    ids, phone numbers and rows of a numeric table. Blocking those would refuse ordinary
    memories and drop the RAG chunks that contain them.
    """
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            yield match.span()


def _has_card_number(text: str) -> bool:
    return next(_card_numbers(text), None) is not None


def secret_spans(content: str) -> list[tuple[int, int]]:
    """Where `is_sensitive` finds a secret in `content`, each as far as it may run,
    merged and in order: what a person is shown blanked. The patterns find where a
    secret starts, not where it stops ("password is correct horse battery staple" is one
    secret, and a token's pattern ends a character into it), so a find runs to the end
    of its line (a card number too: its expiry and code sit beside it) and through the
    lines that carry on a wrapped value, a private key to its END line or the end of the
    text. They are looked for in the text as it reads (`text_safety.searchable`)."""
    text, kept = searchable(content)
    finds = [*_finds(text), *_credential_values(text), *_card_numbers(text)]
    spans = merged([*_to_line_ends(text, finds), *_keys(text)])
    if kept is None:
        return spans
    # Back to where they sit in `content`: a span ends where the next character kept is.
    ends = [*kept, len(content)]
    return [(kept[start], ends[end]) for start, end in spans]


def _finds(text: str) -> Iterator[tuple[int, int]]:
    """Where each pattern matches, looked for again from just past where each find
    starts, not where it ends: a value can run onto the next line and take in the key
    word there, whose own secret, on the line after, was left showing (found in the
    review of 0.35.0)."""
    for pattern in _SENSITIVE:
        at = 0
        while found := pattern.search(text, at):
            yield found.span()
            at = found.start() + 1


def merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """`spans` in order, any that overlap or touch made one."""
    out: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(end, out[-1][1]))
        else:
            out.append((start, end))
    return out


def _to_line_ends(content: str, finds: list[tuple[int, int]]) -> Iterator[tuple[int, int]]:
    """Each find run to the end of its line: at any break `str.splitlines` knows, a lone
    carriage return and a line separator among them (PowerShell, for one, takes a
    carriage return for a new line), and past the lines after it that carry on a value
    wrapped over them. Taken in the order they end, so a line's end is looked for once
    however many finds it holds: looked for from each, 26,000 on one 260,000-character
    line took 5.4 s."""
    line_end = -1
    for start, end in sorted(finds, key=lambda find: find[1]):
        if line_end < end:
            stop = _LINE_BREAK.search(content, end)
            line_end = _past_wrapped(content, stop.start()) if stop else len(content)
        yield start, line_end


def _past_wrapped(content: str, line_end: int) -> int:
    """`line_end`, moved past the lines after it that carry on a value cut at its end:
    its line ends in a token's characters and each line after is nothing else. A token
    cut at 64 characters showed its second half, and taken from any line, a command on
    the next one was hidden (found in the reviews of 0.35.0)."""
    if not _CUT.search(content, max(0, line_end - 18), line_end):
        return line_end
    while line_end < len(content):
        start = line_end + (2 if content.startswith("\r\n", line_end) else 1)
        stop = _LINE_BREAK.search(content, start)
        stop_at = stop.start() if stop else len(content)
        if not _WRAPPED.fullmatch(content, start, stop_at):
            break
        line_end = stop_at
    return line_end


def _keys(content: str) -> Iterator[tuple[int, int]]:
    """Each private key from its BEGIN line through its END line, or to the end of the
    text. An END is looked for again only once a key starts past the one found: 8,000
    BEGIN lines with none took 1.9 s looked for from each."""
    found_at = key_end = -1
    for begin in _PRIVATE_KEY.finditer(content):
        if found_at < begin.end():
            found = _KEY_END.search(content, begin.end())
            found_at, key_end = found.span() if found else (len(content), len(content))
        yield begin.start(), key_end


def is_sensitive(content: str) -> bool:
    """True if the text looks like a credential/secret we must never store."""
    text = searchable(content)[0]
    return (
        any(pattern.search(text) for pattern in _SENSITIVE)
        or _has_credential_value(text)
        or _has_card_number(text)
    )
