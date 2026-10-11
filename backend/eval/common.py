"""What every evaluation shares, so each number is read by the same rules.

- A claim is made when every word of `must` is present, one word of each `any_of`
  group, and no word of `none_of`, after folding case, punctuation and symbols
  ("100°C" says 100), plurals and number words.
  Phrasing is free; the claim is not. Similarity scores can't tell "asked about
  moving" from "is moving", which share every word but one.
- A ratio over nothing is None, never 0.0 or 1.0: no cases of a kind means no
  number for it, not a perfect or a failed one.
- A result file is named by version, family and model, with `_rN` for repeats,
  and an existing file is never replaced.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

from app.telemetry.percentile import nearest_rank

RESULTS = Path(__file__).resolve().parent / "results"

_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
}

# Words that carry no claim. Short on purpose: an eager list deletes the very word
# that separates two claims. "user" is here because every extracted fact starts
# with it.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "to", "of", "in",
        "on", "at", "for", "and", "or", "but", "with", "as", "that", "this", "it",
        "its", "user", "they", "them", "their", "he", "she", "his", "her", "has",
        "have", "had",
    }
)  # fmt: skip

# A time, a price or a decimal stays one word ("07:15", "2,150", "3.5"); anything
# else is letters and digits, with an apostrophe inside a word kept ("don't").
_TOKEN = re.compile(r"\d+(?:[:.,]\d+)+|[^\W_]+(?:'[^\W_]+)*")


def words(text: str) -> frozenset[str]:
    """The content words of `text`, folded so that phrasing doesn't decide a match."""
    folded = unicodedata.normalize("NFKC", text).casefold().replace("’", "'").replace("-", " ")
    found: set[str] = set()
    for token in _TOKEN.findall(folded):
        if token.endswith("'s"):
            token = token[:-2]
        token = _NUMBER_WORDS.get(token, token)
        # "Tuesdays" and "Tuesday" are one claim; applied to both sides, so "class"
        # (kept) and "bus" (short) come out the same on each.
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        if token and token not in _STOPWORDS:
            found.add(token)
    return frozenset(found)


def says(
    text: str,
    must: Sequence[str],
    any_of: Sequence[Sequence[str]] = (),
    none_of: Sequence[str] = (),
) -> bool:
    """Whether `text` makes the claim that `must`, `any_of` and `none_of` identify."""
    said = words(text)
    if not words(" ".join(must)) <= said or words(" ".join(none_of)) & said:
        return False
    return all(words(" ".join(group)) & said for group in any_of)


def ratio(hits: int, total: int) -> float | None:
    return None if total == 0 else round(hits / total, 4)


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile, None for an empty sample rather than a made-up zero."""
    if not values:
        return None
    return round(nearest_rank(sorted(values), fraction), 1)


def median_of(runs: Sequence[Mapping[str, Any]], key: str) -> float | None:
    """The median of one metric across runs, skipping runs that had no value for it."""
    values = [float(run[key]) for run in runs if isinstance(run.get(key), int | float)]
    return None if not values else round(median(values), 4)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def file_hash(path: Path) -> str:
    """Stamped into every result, so a number is always tied to the cases it scored."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")


def result_path(version: str, family: str, model: str, folder: Path = RESULTS) -> Path:
    """The next free name for a result. An earlier run is never overwritten."""
    stem = f"{version}_{family}_{slug(model)}"
    run = 1
    while (folder / f"{stem}_r{run}.json").exists():
        run += 1
    return folder / f"{stem}_r{run}.json"


def write_result(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # "x" refuses an existing file: two runs racing for one name fail loudly.
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
