"""Classify a spoken reply to a confirmation question as yes, no, or neither.

Deterministic and conservative, like `address.py::classify_directive`: tight
patterns, negation wins, and anything uncertain returns `None` rather than a guess.
The kernel fails closed on its own, so the only harm this module can do is treat
something that wasn't consent as consent. That is why `None` is the default and
silence is never an answer.
"""

from __future__ import annotations

import re

# Leading filler is stripped before matching, so "um, yeah go on" still reads as yes.
# "ok" is left out on purpose: it is often filler, but it is also one of the commonest
# ways to say yes, and stripping it would leave a bare "ok" unclassified.
_FILLER = re.compile(r"^(?:u+m+|u+h+|er+|well|so|hmm+)\b[\s,.]*", re.I)

# Negation is searched across the whole utterance, not anchored at the start, so
# "okay, don't" is never read as consent. A refusal token anywhere is enough not to
# act: a missed denial is dangerous, while a missed approval only means asking again.
_NO = re.compile(
    r"\b(?:no|nope|nah|don'?t|do not|stop|cancel|forget it|never ?mind|leave it"
    r"|not (?:now|yet|right now)|hold off|skip it|abort|scrap (?:it|that))\b",
    re.I,
)

_YES = re.compile(
    r"^(?:y(?:es|ep|eah|up|a)|sure|ok(?:ay)?|go (?:ahead|for it|on)|do it|please do"
    r"|that'?s (?:right|fine)|sounds good|fine|alright|all right|confirmed?|affirmative"
    r"|carry on|proceed|of course|absolutely|definitely)\b",
    re.I,
)

# Arabic is supported across the app, so Arabic answers are recognised too.
_YES_AR = re.compile(r"^(?:نعم|أيوه|ايوه|اه|آه|تمام|حسنا|حسناً|موافق|اوكي|أوكي)\b")
_NO_AR = re.compile(r"^(?:لا|لأ|كلا|مش|ماتعملش|توقف|الغ|إلغاء|بلاش)\b")


def classify_answer(text: str) -> bool | None:
    """Return True to approve, False to deny, or None if it isn't an answer.

    `None` covers both "couldn't tell" and "changed the subject". The caller tells
    them apart: the first is worth re-asking, the second means the user moved on.
    """
    cleaned = _FILLER.sub("", text.strip())
    if not cleaned:
        return None
    refuses = bool(_NO.search(cleaned) or _NO_AR.search(cleaned))
    # Consent stays anchored: agreeing is a deliberate opening word, not a token found
    # somewhere in a sentence. Refusal is matched anywhere (see `_NO`).
    agrees = bool(_YES.match(cleaned) or _YES_AR.match(cleaned))

    if refuses and agrees:
        # "okay, don't" and "yes, no problem" look the same to a regex but mean
        # opposite things. Treat both as unclear and let the caller re-ask once.
        return None
    if refuses:
        return False
    if agrees:
        return True
    return None


# An unclassifiable reply counts as a new request, not a mumbled answer, when it is
# long enough or phrased like a question. Short noise is worth one re-ask; a new
# question is not, and re-asking would keep the user stuck on the old one.
_WORDS_THAT_ASK = re.compile(r"\b(?:what|who|where|when|why|how|can you|could you|tell me)\b", re.I)


def looks_like_a_new_request(text: str) -> bool:
    """True if an unclassifiable reply is clearly the user changing the subject."""
    cleaned = text.strip()
    return len(cleaned.split()) >= 4 or bool(_WORDS_THAT_ASK.search(cleaned))
