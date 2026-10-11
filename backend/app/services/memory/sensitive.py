"""The sensitivity guard: content Veronica must never store.

A pure predicate over text with no dependency on the store, the bus or the embedder,
so modules outside MemoryService (the RAG indexer, the persona editor, the audit log)
use it too.
"""

from __future__ import annotations

import re


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
_VALUE = rf"(?!{_NOT_A_VALUE})\S{{3,}}"
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

_SENSITIVE = (
    # keyword followed by a value, config-style (=/:) or spoken ("password is X").
    re.compile(rf"\b({_SECRET_WORDS})\b\s*{_SEP}\s*{_VALUE}", re.I),
    # No "->" here: arrows are flow notation ("Step 1: login -> Step 2: dashboard").
    re.compile(rf"\b({_AMBIGUOUS_WORDS})\b\s*(?:[:=]|/(?=\s))\s*{_VALUE}", re.I),
    # "the alarm pin is 9021" — `pin` alone is ambiguous, so require a digit value, and
    # exclude the codes that are not secrets (zip/area/post/dialling/country/error).
    # "of", "was" and "to" are the extractor's rewordings: "User has a bank PIN of
    # 4821", "User set the garage code to 1122". "To" is safe here only because a digit
    # value is required: "the code to unlock the door" is left alone.
    re.compile(
        r"(?<!\bzip )(?<!\barea )(?<!\bpost )(?<!\bdial )(?<!\bcountry )(?<!\berror )"
        r"\b(pin|code)\b\s*(?:is|are|was|were|of|to|[:=])\s*#?\d{3,}",
        re.I,
    ),
    # "User's PIN 4821": four or more digits straight after `pin`. Fewer are left alone:
    # "the sensor is on pin 25" is wiring, not a secret.
    re.compile(r"\bpin\b\s*#?\d{4,}\b", re.I),
    # "Gate keypad code 4455": an access noun, then "code" and a number. Only
    # punctuation or spaces may sit between "code" and its digits, so documentation
    # like "the fire alarm complies with code BS 5839" isn't taken for an access code.
    re.compile(
        r"\b(gate|door|garage|keypad|alarm|safe|lock)\b[^.\n]{0,20}\bcode\b[^\w\n]{0,4}\d{3,}",
        re.I,
    ),
    re.compile(r"\btokens?\b\s*[:=]\s*\S", re.I),  # "token" only with :/= (more ambiguous)
    re.compile(r"rtsp://[^\s/]*:[^\s/]*@", re.I),  # creds embedded in a stream URL
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub token
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # OpenAI-style key
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack token
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWT
    # (Card numbers are checked separately, by Luhn — see `_has_card_number`.)
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # Arabic is supported throughout, so the guard covers it too:
    # "كلمة السر/المرور" (password), "الرقم السري" (PIN), "رمز" (code).
    re.compile(r"(كلمة\s*(?:السر|المرور)|الرقم\s*السري|رمز\s*(?:الدخول|الأمان))\s*\S*\s*\S{3,}"),
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


def _has_credential_value(text: str) -> bool:
    """A secret noun followed, across only connecting words, by something shaped like a
    credential. The separators above chase connectives, and a paraphrasing extractor
    keeps finding new ones ("a Wi-Fi password named Hunter2"); the value's shape doesn't
    change. A determiner before it makes it a name ("the gate code for the B12
    allotment"), and any other word ends the look: "password manager is 1Password".
    """
    for match in _SECRET_NOUN.finditer(text):
        tail = re.findall(r"[\w#-]+", text[match.end() : match.end() + 48])
        for index, token in enumerate(tail[:_VALUE_TOKENS]):
            if _VALUE_SHAPED.match(token) and (
                index == 0 or tail[index - 1].lower() not in _DETERMINERS
            ):
                return True
            if token.lower() not in _CONNECTIVES:
                break
    return False


def is_secret_name(name: str) -> bool:
    """Whether a field's name says it holds a secret ("password", "api_key", "client
    secret"). The audit log redacts such a field whatever it holds: a passphrase can
    begin with "the"."""
    words = re.sub(r"[_\-.]+", " ", name).strip()
    secret = rf"(?:{_SECRET_WORDS}|{_AMBIGUOUS_WORDS}|tokens?|pin)"
    return bool(re.fullmatch(rf"(?:\w+ )*{secret}", words, re.I))


def _has_card_number(text: str) -> bool:
    """A digit run that actually passes the Luhn checksum.

    Length alone is not enough: 13-16 digit runs are also epoch-ms timestamps, order
    ids, phone numbers and rows of a numeric table. Blocking those would refuse ordinary
    memories and drop the RAG chunks that contain them.
    """
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return True
    return False


def is_sensitive(content: str) -> bool:
    """True if the text looks like a credential/secret we must never store."""
    return (
        any(pattern.search(content) for pattern in _SENSITIVE)
        or _has_credential_value(content)
        or _has_card_number(content)
    )
