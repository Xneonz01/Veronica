"""Text from outside, made safe to hand on and honest to show.

Some characters render as nothing and still reach a model: Unicode tag characters (a
whole sentence can be written in them, invisible on screen and read by the model),
zero-width spaces and joiners, variation selectors where they select nothing (a byte
each, strung after an emoji or spread one per letter), the Hangul fillers, and the
bidirectional controls that make
`Notes_<U+202E>txt.bat` read as `Notes_tab.txt`. A page, a search result or an MCP
server can plant them, so what a person sees and what the model reads disagree. A
joiner between two letters of a script that needs it (Persian, the Indic scripts) is
part of the word, and a variation selector after an emoji, a symbol or an ideograph is
how it picks its form: those stay.

Three uses:

* `clean_untrusted` for text from outside (a page, a search result, a document title):
  everything that renders as nothing is dropped, and line breaks are made plain.
* `clean_local` for the user's own files read back to the model: only the characters
  that exist to hide (tags, bidi controls, zero-width spaces) go, so a carriage return
  or a private-use glyph survives a read-then-write edit.
* `reveal` for a person: the same characters are shown, as `⟪U+202E⟫`, never removed, so
  a prompt can't present one name and approve another.

`display_host` gives the host a request will actually resolve: `httpx` encodes an
international name with IDNA 2008, so `faß.de` is fetched as `xn--fa-hia.de`, while the
older IDNA 2003 encoding shows it as `fass.de`, a different site.

Every such character in this file is written as an escape: one written as itself would
be invisible to whoever reads the code.
"""

from __future__ import annotations

import re
import unicodedata

import httpx

# CSI and OSC escape sequences, and the two-character escapes: a terminal colour code
# from a tool's output is noise to the model and can hide text in a log viewer.
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_TAGS = range(0xE0000, 0xE0080)
_ZWJ = "\u200d"
_JOINERS = frozenset("\u200c\u200d")  # the zero-width non-joiner and joiner
# Letters that render as blank space: the Hangul fillers.
_FILLERS = frozenset("\u115f\u1160\u3164\uffa0")
# Them, and marks that render as nothing, which their category doesn't give away: the
# grapheme joiner, the Khmer inherent vowels Unicode says never to use, and the
# Mongolian free variation selectors. Any number of the last six passed unseen (found
# in the review of 0.35.0).
_BLANK = _FILLERS | frozenset("\u034f\u17b4\u17b5\u180b\u180c\u180d\u180f")
# What exists only to hide or to reorder: kept out of the model's reading of a local
# file, revealed on screen. Bidi embeddings, overrides, isolates and marks; zero-width
# spaces and the word joiner; a byte-order mark anywhere but the start.
_HIDING = frozenset(
    "\u061c\u200b\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2060\u2061\u2062\u2063\u2064\u2066\u2067\u2068\u2069\ufeff"
)
# Categories that render as nothing of their own: format, private use, unassigned and
# surrogate code points (a lone surrogate can arrive from badly decoded JSON).
_UNSEEN = frozenset({"Cf", "Co", "Cn", "Cs"})
# A tab, and the line breaks `str.splitlines` knows that are controls: they lay text
# out rather than hide in it.
_LAYOUT = frozenset("\t\n\r\x0b\x0c\x1c\x1d\x1e\x85")
_EMOJI_FORM = "\ufe0f"
# What ASCII holds that renders as nothing: the controls, a tab and line breaks aside.
_ASCII_UNSEEN = re.compile(r"[\x00-\x08\x0e-\x1b\x1f\x7f]")
# The emoji whose characters aren't symbols by category (double exclamation,
# interrobang, information, wavy dash, part alternation mark), and the keycaps' bases,
# which take an emoji form only before the keycap mark.
_EMOJI_BASES = frozenset("\u203c\u2049\u2139\u3030\u303d")
_KEYCAP_BASES = frozenset("0123456789#*")
_KEYCAP = "\u20e3"
_BREAKS = frozenset({"Zl", "Zp"})


def _joins(text: str, index: int) -> bool:
    """Whether the joiner at `index` does a joiner's work: between two letters, one of
    them outside ASCII (it shapes Persian and the Indic scripts; between two Latin
    letters it only hides), or a zero-width joiner between two symbols, which builds
    one emoji (a family, a flag), the first maybe in its emoji form. Never beside
    something that renders as nothing: between two dropped selectors, a kept joiner
    carried a bit (found in the review of 0.35.0)."""
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    if not before or not after:
        return False
    if _letter(before) and _letter(after) and not (before + after).isascii():
        return True
    return (
        text[index] == _ZWJ
        and _symbolic(after)
        and (_symbolic(before) or (before == _EMOJI_FORM and not _hidden(text, index - 1)))
    )


def _letter(char: str) -> bool:
    # A mark counts (the virama before a joiner), unless it renders as nothing.
    return unicodedata.category(char)[0] in "LM" and not (_variation(char) or char in _BLANK)


def _symbolic(char: str) -> bool:
    return unicodedata.category(char) in ("So", "Sk") or "\U0001f3fb" <= char <= "\U0001f3ff"


def _variation(char: str) -> bool:
    return "\ufe00" <= char <= "\ufe0f" or "\U000e0100" <= char <= "\U000e01ef"


def _selects(char: str, before: str, after: str) -> bool:
    """Whether the variation selector `char` picks a registered form of `before`, as
    Unicode's variation sequences do: text or emoji presentation (U+FE0E, U+FE0F) after
    a symbol beyond ASCII or an emoji, or a keycap's base right before its keycap mark;
    a math variant (U+FE00-FE0D) after a math symbol beyond ASCII; one of the first three
    after a CJK ideograph, U+FE00 after a Myanmar letter, two after a Phags-pa one; the
    supplement after a CJK ideograph. Anywhere else it carries nothing but itself."""
    code = ord(char)
    if code >= 0xE0100:
        return _ideograph(before)
    if code >= 0xFE0E:
        if before in _KEYCAP_BASES:
            return after == _KEYCAP
        symbol = unicodedata.category(before) in ("So", "Sk", "Sm") and not before.isascii()
        return symbol or before in _EMOJI_BASES
    return (
        (unicodedata.category(before) == "Sm" and not before.isascii())
        or (code <= 0xFE02 and _ideograph(before))
        or (code == 0xFE00 and "\u1000" <= before <= "\u109f")
        or (code <= 0xFE01 and "\ua840" <= before <= "\ua877")
    )


def _ideograph(char: str) -> bool:
    """A CJK ideograph, unified or compatibility: what `lib/reveal.ts` reads as
    `Unified_Ideograph` and the two compatibility blocks."""
    name = unicodedata.name(char, "")
    return name.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH"))


def _hidden(text: str, index: int) -> bool:
    """Whether `text[index]` renders as nothing in a model's reading: a control other
    than a tab or a line break, a format character but a joiner at work, private use,
    unassigned, a blank letter or a mark that shows nothing, or a variation selector
    that selects nothing: one not right after a visible character it picks a form of."""
    char = text[index]
    if char in "\n\t":
        return False
    if char in _BLANK:
        return True
    if _variation(char):
        before = text[index - 1] if index > 0 else ""
        after = text[index + 1] if index + 1 < len(text) else ""
        return (
            not before
            or _variation(before)
            or _hidden(text, index - 1)
            or not _selects(char, before, after)
        )
    category = unicodedata.category(char)  # tag characters are format (`Cf`) too
    if category == "Cc":
        return True
    if category in _UNSEEN:
        return not (char in _JOINERS and _joins(text, index))
    return False


def searchable(text: str) -> tuple[str, list[int] | None]:
    """`text` as a search for a word reads it, and where each of its characters sits in
    `text` (None when none went): what renders as nothing taken out, a joiner or a
    selector at work included, and a letter that renders as blank space read as a
    space. A soft hyphen inside "password" hid it from the check for secrets, and so did
    Hangul fillers between its words (found in the reviews of 0.35.0). Each kind of
    character is asked about once: 6 ms on 256 KB of Arabic, 42 asking about each one."""
    if text.isascii() and not _ASCII_UNSEEN.search(text):
        return text, None
    table = {ord(char): read for char in set(text) if (read := _read_as(char)) != char}
    if not table:
        return text, None
    gone = {chr(code) for code, read in table.items() if not read}
    kept = [index for index, char in enumerate(text) if char not in gone] if gone else None
    return text.translate(table), kept


def _read_as(char: str) -> str:
    """What `char` is to a search for a word: nothing, a space, or itself. A tab and a
    line break lay text out, and stay."""
    if char in _LAYOUT:
        return char
    if char in _FILLERS:
        return " "
    category = unicodedata.category(char)
    if category == "Cc" or category in _UNSEEN or char in _BLANK or _variation(char):
        return ""
    return char


def _plain_lines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def clean_untrusted(text: str) -> str:
    """`text` with everything that renders as nothing removed, and line breaks plain."""
    text = _plain_lines(_ANSI.sub("", text))
    kept = []
    for index, char in enumerate(text):
        if unicodedata.category(char) in _BREAKS:
            kept.append("\n")
        elif not _hidden(text, index):
            kept.append(char)
    return "".join(kept)


def clean_local(text: str) -> str:
    """The user's own file as the model should read it: only what hides goes, a
    variation selector that selects nothing among it."""
    return "".join(
        char
        for index, char in enumerate(text)
        if ord(char) not in _TAGS
        and (char not in _HIDING or (char == "\ufeff" and index == 0))
        and not (_variation(char) and _hidden(text, index))
    )


def reveal(text: str) -> str:
    """`text` with every character that renders as nothing shown as `⟪U+XXXX⟫`, so a
    person reads what the model reads. Line breaks and tabs stay as they are."""
    out = []
    for index, char in enumerate(text):
        if char == "\r":
            out.append("⟪U+000D⟫" if not text.startswith("\n", index + 1) else "")
        elif _hidden(text, index) or unicodedata.category(char) in _BREAKS:
            out.append(f"⟪U+{ord(char):04X}⟫")
        else:
            out.append(char)
    return "".join(out)


def has_hidden(text: str) -> bool:
    """Whether `text` holds a character that renders as nothing (a tab or a line
    break aside): a name that should be refused rather than written. Private use is
    allowed: a file shared from a Mac keeps `:` and its kin as private-use characters,
    which show as a glyph."""
    return "\r" in text or any(
        _hidden(text, index) and unicodedata.category(char) != "Co"
        for index, char in enumerate(text)
    )


def display_host(url: str) -> tuple[str, str]:
    """The host a request to `url` resolves, as ASCII (punycode for an international
    name), and as people read it. Both empty when `url` isn't a URL with a host; spaces
    around it don't count, as the request leaves them out."""
    try:
        parsed = httpx.URL(url.strip())
    except (httpx.InvalidURL, TypeError, ValueError):
        return "", ""
    ascii_host = parsed.raw_host.decode("ascii", "replace")
    return ascii_host, reveal(parsed.host)
