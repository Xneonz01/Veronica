"""How many tokens a text takes on the language model, estimated from above.

Ollama has no endpoint that counts tokens (0.33.3), and a prompt larger than the
model's window isn't refused: the front of it, her persona first, is silently dropped.
So the chat window (`services/chat/window.py`) counts with this estimate, built from
what qwen3.5:9b's tokenizer was measured to do (Ollama 0.33.3, 2026-09-26: the texts in
`eval/tokens.py` through `scripts/token_probe.py`, and every range of Unicode against
the tokenizer rebuilt from the model file):

- every ASCII digit is a token of its own;
- a word that reads like language costs about a token per 5 letters, but letters that
  don't (random letters, a DNA sequence, a key spelled in letters, letters between
  digits as in hex or base64) take a token per 1.5 to 2;
- a space before a word is part of it, and other whitespace costs little;
- natural text in Greek, Cyrillic, Hebrew, Arabic, Devanagari or Thai costs under a
  token per two letters, and Chinese, Japanese or Korean under one a character;
- no character costs more tokens than its UTF-8 bytes, and the everyday symbols (curly
  quotes, dashes, the euro, arrows) cost one.

Measured classes are charged their measured rates, and everything else its bytes. On
the 39 texts of `eval/tokens.py` the estimate is at or above the model's count: data
such as logs and JSON up to 16% above, English prose 22%, code 1.6 to 1.7 times, the
thirteen other languages up to 2.4 times (Thai), and a text of nothing but spaces or
blank lines up to 12 times. What it can undercount is random letters of a script it
charges by measured rates, which natural text in it isn't. The rules describe one
tokenizer: before trusting them with another model, measure it with the probe.
"""

from __future__ import annotations

import math
import re
import unicodedata
from bisect import bisect_right
from collections.abc import Iterator

# A word that reads like language, and letters that don't.
_LETTERS_PER_TOKEN = 5.0
_DENSE_LETTERS_PER_TOKEN = 1.5
# A word this long is judged by its vowels (y counted): English has one in about every
# 2.5 letters, random letters one in 4.3. Shorter words are too few letters to judge
# alone, so each line is judged instead, by a, e, i, o and u, which random letters have
# one in 5.2 of, and English one in 2.7 even in a run of "text" or "word": a line that
# isn't language is dense throughout. By the line, so text put together (a result in
# its fence) costs what its parts do.
_JUDGED_FROM = 8
_WORD_VOWEL_SHARE = 0.30
_LINE_VOWEL_SHARE = 0.22
_LINE_JUDGED_FROM = 100
_WHITESPACE_PER_TOKEN = 8
# Scripts measured on natural text: a token per two letters, or per character for the
# ones written a syllable or a word at a time; full-width Latin letters, two.
_OTHER_SCRIPT = 0.5
_WIDE_SCRIPT = 1.0
_FULL_WIDTH = 2
# The letters those texts are written in, not the whole of each block: its rarer
# letters (historic Cyrillic, the CJK extensions) cost two or three tokens each.
_MEASURED_SCRIPTS = (
    (0x0386, 0x03CE),  # Greek
    (0x0400, 0x045F),  # Cyrillic
    (0x0591, 0x05EA),  # Hebrew, with its points
    (0x0620, 0x065F),  # Arabic, with its vowel marks
    (0x0900, 0x0963),  # Devanagari
    (0x0E01, 0x0E5B),  # Thai
)
_WIDE = (
    (0x3041, 0x30FF),  # kana
    (0x4E00, 0x9FFF),  # CJK ideographs
    (0xAC00, 0xD7A3),  # Hangul syllables
)
# Latin letters measured in French, German, Polish and Vietnamese: a word of them costs
# what an English one does, and a token more for each one with a diacritic.
_MEASURED_LATIN = ((0x00C0, 0x017F), (0x1EA0, 0x1EFF))
# One token each in running text, measured: the symbols people type, and Chinese and
# Japanese punctuation.
_ONE_TOKEN = frozenset("’‘“”—–…•·«»°€£¥©®×→←↑↓✓±§¶、。「」『』々")
_COMBINING = (0x0300, 0x036F)  # a mark on a letter: one token, measured
_FULL = ((0xFF00, 0xFFEF),)


def _class(*ranges: tuple[int, int]) -> str:
    """A regular expression's character class for `ranges`, counted in C, not per letter."""
    return "".join(f"{re.escape(chr(low))}-{re.escape(chr(high))}" for low, high in ranges)


_ASCII_LETTER = re.compile("[A-Za-z]")
_LATIN_LETTER = re.compile(f"[{_class(*_MEASURED_LATIN)}]")
_SCRIPT_LETTER = re.compile(f"[{_class(*_MEASURED_SCRIPTS)}]")
_WIDE_LETTER = re.compile(f"[{_class(*_WIDE)}]")
_FULL_LETTER = re.compile(f"[{_class(*_FULL)}]")
_MEASURED = (*_MEASURED_LATIN, *_MEASURED_SCRIPTS, *_WIDE, *_FULL)
_KNOWN_LETTER = re.compile(f"[A-Za-z{_class(*_MEASURED)}]")
_ONE_TOKEN_MARK = re.compile(f"[{_class(_COMBINING, *_MEASURED_SCRIPTS)}]")

# One piece at a time, as the tokenizer splits before it merges: a run of word
# characters, a run of whitespace, or any other single character.
_PIECE = re.compile(r"(\w+)|(\s+)|(.)", re.DOTALL)
_SEGMENT = re.compile(r"(_*)([^_]*)")
_PART = re.compile(r"(\d+)|([^\W\d_]+)")
_NOT_ASCII_LETTER = re.compile(r"[^A-Za-z]+")
_WITHOUT_VOWELS = str.maketrans("", "", "aeiouyAEIOUY")
_WITHOUT_PLAIN_VOWELS = str.maketrans("", "", "aeiouAEIOU")


def estimate(text: str) -> int:
    """At least the tokens `text` takes, by the measurements above."""
    return sum(cost for _, cost in _costs(text))


def fewest_tokens(chars: int) -> int:
    """No text of `chars` characters is estimated below this: whitespace and underscores,
    the cheapest pieces, cost a token per 8. A text longer than 8 times the room it has
    can be refused without reading it."""
    return chars // _WHITESPACE_PER_TOKEN


def clip(text: str, budget: int) -> str:
    """The longest start of `text` whose own estimate is within `budget`, cut between
    pieces. Checked whole: the line it cuts is judged again, and can read differently."""
    cut = text[: _end_within(text, budget)]
    while (over := estimate(cut) - budget) > 0:
        cut = cut[: _end_within(cut, budget - over)]
    return cut


def _end_within(text: str, budget: int) -> int:
    used = end = 0
    for stop, cost in _costs(text):
        if used + cost > budget:
            break
        used += cost
        end = stop
    return end


def _costs(text: str) -> Iterator[tuple[int, int]]:
    """Each piece's end in `text` and what it costs."""
    starts, noisy = _lines(text)
    for match in _PIECE.finditer(text):
        word, space, other = match.groups()
        if word:
            cost = _word(word, noisy[bisect_right(starts, match.start()) - 1])
        elif space:
            cost = _whitespace(space, text[match.end() : match.end() + 1])
        else:
            cost = _other(other)
        yield match.end(), cost


def _lines(text: str) -> tuple[list[int], list[bool]]:
    """Where each line starts, and whether its letters read as noise."""
    starts: list[int] = []
    noisy: list[bool] = []
    position = 0
    for line in text.split("\n"):
        starts.append(position)
        noisy.append(_reads_as_noise(line))
        position += len(line) + 1
    return starts, noisy


def _reads_as_noise(line: str) -> bool:
    """Whether a line's Latin letters have too few vowels to be language."""
    letters = _NOT_ASCII_LETTER.sub("", line)
    if len(letters) < _LINE_JUDGED_FROM:
        return False
    vowels = len(letters) - len(letters.translate(_WITHOUT_PLAIN_VOWELS))
    return vowels < _LINE_VOWEL_SHARE * len(letters)


def _whitespace(run: str, follows: str) -> int:
    """Line breaks are a piece of their own, and so is the indent after them, less a
    lone space: that joins the word after it, though not a digit, which stands alone.
    A space that isn't ASCII (a thin or a non-breaking one) costs its bytes."""
    unusual = sum(len(ch.encode()) for ch in run if not ch.isascii())
    if unusual:
        run = "".join(ch for ch in run if ch.isascii())
    cut = max(run.rfind("\n"), run.rfind("\r")) + 1  # 0 when there is no line break
    breaks, indent = run[:cut], run[cut:]
    cost = unusual + math.ceil(len(breaks) / _WHITESPACE_PER_TOKEN)
    if indent and (indent != " " or follows.isdigit()):
        cost += math.ceil(len(indent) / _WHITESPACE_PER_TOKEN)
    return cost


def _other(ch: str) -> int:
    """A character that is neither a word's nor a space."""
    if ch.isascii() or ch in _ONE_TOKEN or _ONE_TOKEN_MARK.match(ch):
        return 1
    return len(ch.encode())


def _word(run: str, dense: bool) -> int:
    if run.isascii() and run.isalpha():  # the common case: a plain English word
        return _latin(run, dense)
    total = 0.0
    for underscores, body in _SEGMENT.findall(run):
        # One underscore joins the piece after it; a run of them is a piece of its own.
        if len(underscores) > 1 or (underscores and not body):
            total += math.ceil(len(underscores) / _WHITESPACE_PER_TOKEN)
        parts = _PART.findall(body)
        mixed = len(parts) > 1  # letters among digits: hex, base64, keys
        for digits, letters in parts:
            if digits:
                total += sum(len(digit.encode()) for digit in digits)
            else:
                total += _letters(letters, dense or mixed)
    return math.ceil(total)


def _letters(run: str, dense: bool) -> float:
    if run.isascii():
        return _latin(run, dense)
    plain = len(_ASCII_LETTER.findall(run))
    latin = len(_LATIN_LETTER.findall(run))
    if plain + latin == len(run):
        # A letter with a diacritic costs one more: Polish, the densest Latin script
        # measured, needs it.
        return _latin(run, dense) + latin
    rate = _DENSE_LETTERS_PER_TOKEN if dense else _LETTERS_PER_TOKEN
    never_measured = _KNOWN_LETTER.sub("", run)  # the most it can take: its bytes
    return (
        plain / rate
        + latin * (1 + 1 / _LETTERS_PER_TOKEN)
        + len(_SCRIPT_LETTER.findall(run)) * _OTHER_SCRIPT
        + len(_WIDE_LETTER.findall(run)) * _WIDE_SCRIPT
        + len(_FULL_LETTER.findall(run)) * _FULL_WIDTH
        + len(never_measured.encode())
    )


def _latin(run: str, dense: bool) -> int:
    rate = _LETTERS_PER_TOKEN
    if dense or (len(run) >= _JUDGED_FROM and _vowels(run) < _WORD_VOWEL_SHARE * len(run)):
        rate = _DENSE_LETTERS_PER_TOKEN
    return math.ceil(len(run) / rate)


def _vowels(run: str) -> int:
    # Taken apart, an accented vowel is a vowel and its mark: "préférées" is language.
    plain = run if run.isascii() else unicodedata.normalize("NFD", run)
    return len(plain) - len(plain.translate(_WITHOUT_VOWELS))
