"""Telling real text from OCR noise.

Apple's Vision confidence is close to useless as a filter -- it reports ~0.5
for nearly everything, confident garbage included. So plausibility is judged
on the text itself.

The asymmetry that drives every threshold here: speaking gibberish is worse
than staying quiet. A blind user cannot glance at the sign to check, so a
confident misreading is not a small error, it is misinformation. When in
doubt, drop the line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VOWELS = set("aeiouAEIOU")

# Characters an OCR engine produces from non-text structure. Vertical
# strokes (barcodes, blinds, railings) collapse into the first family;
# rings and badges into the second. A token drawn entirely from one
# family is structure, not writing.
_CONFUSABLE_FAMILIES = (
    set("il1|![]/\\"),
    set("0oq@°"),
    set("-_=~."),
)

# Letter combinations that never start an English word. Cheap, and they catch
# a lot of the consonant soup OCR produces from texture and noise.
_IMPOSSIBLE_STARTS = re.compile(
    r"^(?:[bcdfghjklmnpqrstvwxz]{4}|[^a-zA-Z0-9]{2})", re.IGNORECASE
)

# Strings worth keeping even though they look nothing like words: room
# numbers, exits, platform codes. These are exactly what signage carries.
_MEANINGFUL_SHORT = re.compile(
    r"^(?:[A-Z]{1,4}[- ]?\d{1,4}[A-Z]?|\d{1,4}[A-Z]?|"
    r"(?:exit|in|out|up|down|wc|men|women|open|shut|stop|push|pull|no|yes))$",
    re.IGNORECASE,
)

_WORDLIKE = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")

# Punctuation that legitimately hugs a word. Apostrophes and internal hyphens
# survive because they belong to the word itself.
_EDGE_PUNCTUATION = ".,;:!?\"'`()[]{}<>|\\/*_~^&@#$%+= � "


def _strip_edges(token: str) -> str:
    return token.strip(_EDGE_PUNCTUATION)


def clean_for_speech(text: str) -> str:
    """Tidy a recognized line before it is spoken.

    Stray marks that survive recognition are noise, not content, and a screen
    reader voices them literally -- "EXIT comma comma".
    """
    tokens = [_strip_edges(t) for t in re.split(r"\s+", text.strip())]
    return " ".join(t for t in tokens if t)


@dataclass(frozen=True, slots=True)
class Plausibility:
    keep: bool
    score: float
    reason: str


def _is_stroke_artifact(token: str) -> bool:
    """Is this token just repeated strokes or circles resolved into glyphs?

    Packaging is full of structure that is not text, and engines resolve it
    predictably: barcode bars become I, l, 1 or |; circular badges and
    nutrition rings become 0, O or Q. Observed on a symbol-only corpus --
    "Il", "Illl" from a barcode and "00", "0000" from nutrition icons.

    A token drawn entirely from one of those confusable sets is an artifact.
    Mixing sets is the tell for real content: "100" spans both and is fine,
    "000" does not and is a row of rings.
    """
    if len(token) < 2:
        return False
    lowered = token.lower()
    return any(all(ch in family for ch in lowered) for family in _CONFUSABLE_FAMILIES)


def _token_is_plausible(token: str) -> bool:
    """Could this token be a word, a number, or a code?"""
    if not token:
        return False
    if _is_stroke_artifact(token):
        return False
    if _MEANINGFUL_SHORT.match(token):
        return True
    if token.isdigit():
        return True
    if _WORDLIKE.match(token):
        # "a" and "I" are the only single-letter English words. Anything
        # else of length 1 is a stray mark -- observed output included
        # "J 44 Elevator", where the "J" is the sign's border.
        if len(token) == 1:
            return token.lower() == "a" or token == "I"
        # A run of letters with no vowel is not a word in any language using
        # this alphabet. Observed junk is short: "JQ", "JJ", "th", "fik".
        # Genuine vowelless signage ("WC", "ID") is matched by
        # _MEANINGFUL_SHORT before reaching here.
        if not any(ch in _VOWELS for ch in token):
            return False
        return True
    # Mixed alphanumeric like "B12" or "A-4" is common on signage.
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9\-/.]*$", token) and any(
        ch.isalnum() for ch in token
    ):
        return True
    return False


def assess(text: str, confidence: float) -> Plausibility:
    """Decide whether a recognized line is worth speaking."""
    stripped = text.strip()

    if len(stripped) < 2:
        return Plausibility(False, 0.0, "too short")

    # A line that is mostly punctuation is decoration or noise, not text.
    alnum = sum(ch.isalnum() for ch in stripped)
    if alnum / len(stripped) < 0.5:
        return Plausibility(False, 0.0, "mostly symbols")

    if _IMPOSSIBLE_STARTS.match(stripped):
        return Plausibility(False, 0.0, "implausible opening")

    # Strip punctuation clinging to each token, and drop tokens that are only
    # punctuation. OCR routinely appends stray marks to text it read
    # correctly -- "EXIT,," and "Conference Room B ." were both being
    # discarded as gibberish. Real garbage has punctuation interspersed
    # through the token and still fails the checks below.
    tokens = [_strip_edges(t) for t in re.split(r"\s+", stripped)]
    tokens = [t for t in tokens if t]
    if not tokens:
        return Plausibility(False, 0.0, "punctuation only")

    plausible = [t for t in tokens if _token_is_plausible(t)]
    ratio = len(plausible) / len(tokens)

    # A single junk token in an otherwise good line is tolerable; half of them
    # is not, because the user cannot tell which half was wrong.
    if ratio < 0.6:
        return Plausibility(False, ratio, "mostly implausible tokens")

    letters = sum(ch.isalpha() for ch in stripped)
    if letters >= 4:
        vowels = sum(ch in _VOWELS for ch in stripped)
        vowel_ratio = vowels / letters
        # English runs ~38%. Outside this band it is not prose, though short
        # codes legitimately have no vowels and are handled above.
        if vowel_ratio < 0.12 or vowel_ratio > 0.72:
            return Plausibility(False, ratio, f"vowel ratio {vowel_ratio:.2f}")

    return Plausibility(True, round(ratio * max(confidence, 0.3), 3), "ok")


def is_plausible(text: str, confidence: float = 0.5) -> bool:
    return assess(text, confidence).keep


def normalize(text: str) -> str:
    """Collapse a line for cross-frame comparison."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())
