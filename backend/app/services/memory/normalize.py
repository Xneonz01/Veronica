"""Text normalization for lexical matching + dedupe (Arabic-aware).

Arabic is first-class for Veronica, so normalization folds the common
orthographic variants (alef/ya/ta-marbuta forms), strips diacritics + tatweel,
and applies NFKC + casefold so lexical overlap matches across spellings.
"""

from __future__ import annotations

import re
import unicodedata

# Arabic diacritics (harakat, tanwin, superscript alef, ...) and the tatweel.
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_ARABIC_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def normalize(text: str) -> str:
    """Casefold + NFKC + Arabic folding; collapse whitespace. Stable + idempotent."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _DIACRITICS.sub("", folded).replace(_TATWEEL, "")
    folded = folded.translate(_ARABIC_FOLD)
    return re.sub(r"\s+", " ", folded).strip()


def tokenize(text: str) -> set[str]:
    """Normalized token set."""
    return {tok for tok in _TOKEN_SPLIT.split(normalize(text)) if tok}


# High-frequency function words dropped before lexical-overlap scoring, so two
# texts that merely share "the"/"is"/"في" don't read as relevant (the relevance gate
# would otherwise pass a stopword-only match). Normalized at load to compare
# against tokenize() output.
_RAW_STOPWORDS = (
    "a an the and or but if then of to in on at by for with from into about as "
    "is are was were be been being am do does did doing have has had i you he she "
    "it we they me him her us them my your his its our their this that these those "
    "what which who whom how when where why can could will would shall should may "
    "might must not no yes so than too very just get got also "
    "في من على الى عن مع هذا هذه ذلك التي الذي ما ماذا هل كيف اين متى لماذا هو هي "
    "انا انت نحن هم ان او لا نعم كان يكون قد عند بعد قبل"
).split()
_STOPWORDS = {normalize(word) for word in _RAW_STOPWORDS}


def content_tokens(text: str) -> set[str]:
    """Normalized tokens minus high-frequency stopwords (for lexical scoring)."""
    return {tok for tok in tokenize(text) if tok not in _STOPWORDS}
