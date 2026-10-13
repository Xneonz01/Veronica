"""What a permission prompt shows of a request: where it goes, and every argument as it is.

A prompt is the one place a person checks what an action will do, and the action's
arguments may be an attacker's text. So nothing is hidden and nothing is cut silently:

* every character that renders as nothing is shown (`core/text_safety.reveal`): a bidi
  override would make `Notes_<U+202E>txt.bat` read as `Notes_tab.txt`;
* the target comes first: the host a page is read from, as the request resolves it
  (punycode, so a Cyrillic "аpple.com" can't pass for Apple's), or the file written;
* a long value is clipped where it says so (`more`), and the whole of it is fetched on
  request (Show all, `GET /capabilities/pending/{token}`), not broadcast with the event;
* what reads as a secret is blanked on the card, found in the whole of a value and not
  just the part shown, from where it starts to the end of its line
  (`sensitive.secret_spans`), and the blank says how much it hid; a list or an object
  is screened string by string. The card goes to
  every window and its summary is said aloud on a call, so nothing of a secret is on it;
  the summary is built from the arguments already blanked (`summarized`), so cutting it
  can't leave the start of one. A file's path is never blanked: where it goes is what
  the card is for.
* blanked, a secret could hide what an action does: a page could put a script behind a
  `$password='x';` of its own. So the whole of each argument (Show all) is not blanked,
  but for a field named for a secret. The app asks for it only in the window showing
  the card; the route answers whatever holds the token and can reach the API, as every
  route does (SECURITY.md).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.core.text_safety import display_host, reveal
from app.kernel.registry import Capability
from app.services.memory.sensitive import is_secret_name, merged, secret_spans

MAX_SHOWN = 300
REDACTED = "[redacted]"  # a field named for a secret, whose length isn't shown either
# Where an action lands is what its card is for: a file's path is never blanked, however
# its name reads. Blanked, `password=x1.ps1` hid where a script was going.
_WHERE = "path"


def prompt(
    capability: Capability, args: BaseModel, asked: str | None
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Everything a prompt shows of a request: its line, the card, and Show all's values.
    A line a check before asking wrote (`asked`) is kept as it is: the file write's names
    where the file really goes and its size, never its content, and the memory ones
    refuse a secret before anyone is asked. Otherwise the capability sums up its
    arguments, blanked first."""
    line = reveal(asked) if asked else summarized(capability, args)
    display, full = describe(capability, args.model_dump())
    return line, display, full


def describe(
    capability: Capability, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """(what the card shows, every argument in full for Show all), for a request whose
    validated arguments dumped to `values`: the card's blanked and clipped, the full ones
    revealed but not blanked, but for a field named for a secret. A list or an object
    is shown as JSON, each string in it screened on its own."""
    full = {name: _text(_leaves(name, value, _unblanked)[0]) for name, value in values.items()}
    target, read_as = _target(capability, values)
    details = []
    for name, value in values.items():
        screened_value, hidden = _field(name, value)
        shown = _text(screened_value)
        details.append(
            {
                "field": name,
                "value": shown[:MAX_SHOWN],
                "more": max(0, len(shown) - MAX_SHOWN),
                "masked": hidden,
            }
        )
    return {"target": target, "target_read_as": read_as, "details": details}, full


def screened(name: str, text: str) -> tuple[str, int]:
    """One argument as the card shows it, and how many of its characters were blanked:
    what reads as a secret blanked, the rest revealed. Read as "<name> is <value>" too:
    a field named `code` holding 4455."""
    if is_secret_name(name):
        return REDACTED, len(text)
    lead = f"{name} is "
    named = [(max(0, a - len(lead)), b - len(lead)) for a, b in secret_spans(lead + text)]
    return _blanked(text, merged([(a, b) for a, b in named if b > 0] + secret_spans(text)))


def summarized(capability: Capability, args: BaseModel) -> str:
    """The card's line for a request no check before asking named, which every window is
    sent and a call says aloud: the capability's own summary of its arguments, each
    blanked first, so a summary that cuts a value cuts the blank, never the secret. Where
    the action lands, a path or an address, is left as it is. Only what screening
    changed is replaced, so every other argument keeps its type."""
    blanked = {}
    for name, value in args.model_dump().items():
        shown = value if name in capability.url_fields else _field(name, value)[0]
        if shown != value:
            blanked[name] = shown
    return reveal(capability.summarize(args.model_copy(update=blanked)))


def _field(name: str, value: object) -> tuple[object, int]:
    """An argument as the card shows it. Where the action lands is what the card is
    for, so a file's path is never blanked, however its name reads: blanked,
    `password=x1.ps1` hid where a script was going. A key named `path` inside an object
    is just a key."""
    if name == _WHERE and isinstance(value, str):
        return reveal(value), 0
    return _leaves(name, value, screened)


def _leaves(
    name: str, value: object, leaf: Callable[[str, str], tuple[str, int]]
) -> tuple[object, int]:
    """`value` with `leaf` applied to each string, key and number in it, read under the
    name of the field or key that holds it, and what the calls hid in all. A list was
    screened as its JSON, where a line break is two characters, so a secret on its
    second line showed (found in the review of 0.35.0). Whatever a field named for a
    secret holds is blanked. A number is left a number unless it is blanked."""
    if isinstance(value, str):
        return leaf(name, value)
    if is_secret_name(name):
        return REDACTED, len(_text(value))
    if isinstance(value, dict):
        out: dict[str, object] = {}
        hidden = 0
        for key, item in value.items():
            shown_key, key_hidden = leaf(name, str(key))
            while shown_key in out:  # two keys blanked alike stay two
                shown_key += " "
            out[shown_key], count = _leaves(str(key), item, leaf)
            hidden += key_hidden + count
        return out, hidden
    if isinstance(value, list | tuple):
        done = [_leaves(name, item, leaf) for item in value]
        return [shown for shown, _ in done], sum(count for _, count in done)
    if isinstance(value, int | float):
        shown, hidden = leaf(name, json.dumps(value))
        return (shown, hidden) if hidden else (value, 0)
    return value, 0


def _unblanked(name: str, text: str) -> tuple[str, int]:
    """A string as Show all gives it: revealed, blanked only in a field named for a secret."""
    return (REDACTED, 0) if is_secret_name(name) else (reveal(text), 0)


def _blanked(text: str, spans: list[tuple[int, int]]) -> tuple[str, int]:
    """`text` revealed, each of `spans` (in order, apart) a blank that says how much it
    hid, and the characters hidden in all."""
    out, at, hidden = [], 0, 0
    for start, end in spans:
        out += [reveal(text[at:start]), f"[redacted: {end - start} characters]"]
        hidden += end - start
        at = end
    return "".join([*out, reveal(text[at:])]), hidden


def _text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _target(capability: Capability, values: dict[str, Any]) -> tuple[str, str]:
    """Where the action lands, and how its host reads in Unicode when that differs."""
    for name in sorted(capability.url_fields):
        url = values.get(name)
        if isinstance(url, str):
            host, read_as = display_host(url)
            return host, read_as if read_as != host else ""
    path = values.get("path")
    return (reveal(path), "") if isinstance(path, str) else ("", "")
