"""Text preparation for TTS: strip markup/emoji and split into sentences."""

from __future__ import annotations

import re

_EMOJI = re.compile(
    "["
    "\U0001f300-\U0001faff"  # symbols, pictographs, emoji
    "\U00002600-\U000027bf"  # misc symbols + dingbats
    "\U0001f1e6-\U0001f1ff"  # regional indicators (flags)
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "]+",
    flags=re.UNICODE,
)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_MARKS = re.compile(r"[*_`~#>]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Words whose full stop doesn't end a sentence. Split there, "Ask Dr. Smith" was spoken
# as "Ask Dr." and then "Smith", two utterances with a finished fall in the middle. A
# sentence that really does end on one is joined to the next, which reads as the two
# sentences it is. Not here: "no" and "etc", which end sentences far more often than
# they abbreviate, and single capitals ("grade A.").
_ABBREVIATIONS = frozenset(
    ["dr", "mr", "mrs", "ms", "prof", "st", "jr", "sr", "vs", "e.g", "i.e", "a.m", "p.m"]
)


def sanitize_for_tts(text: str) -> str:
    """Strip markdown and emoji so the synthesizer never voices stray symbols."""
    text = _MD_LINK.sub(r"\1", text)
    text = text.replace("```", " ")
    text = _MD_MARKS.sub("", text)
    text = _EMOJI.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def ends_on_abbreviation(text: str) -> bool:
    """Whether the full stop `text` ends on belongs to an abbreviation, not a sentence."""
    stripped = text.rstrip().rstrip("\"')]”’")
    if not stripped.endswith("."):
        return False
    words = stripped[:-1].split()
    return bool(words) and words[-1].lower().lstrip("([\"'“‘") in _ABBREVIATIONS


def split_sentences(text: str) -> list[str]:
    """Split into sentences so each can be synthesized as soon as it is ready."""
    sentences: list[str] = []
    held = ""
    for part in _SENTENCE_SPLIT.split(text.strip()):
        if not part.strip():
            continue
        held = f"{held} {part.strip()}" if held else part.strip()
        if not ends_on_abbreviation(held):
            sentences.append(held)
            held = ""
    if held:
        sentences.append(held)
    return sentences
