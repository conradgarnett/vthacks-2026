"""Correcting near-miss reads against a vocabulary of real-world wording.

Cursive faces fail in a specific way. The lead-in flourish of a capital gets
read as part of the background, so the first letter is lost and everything
else survives -- "Reception" comes back as "eception", "Room 204B" as
"Roo 204B", "Fire Exit" as "ire gxit". Those are one edit from correct.

The danger is obvious and is why the threshold is high. Snapping loosely turns
an unreadable smear into a confident, wrong word, and a user who cannot see
the label has no way to catch it. So:

  - only words already in the vocabulary below are ever produced
  - the match must be very close (>= 0.85), which is one wrong or missing
    character in a short word
  - a token that is already a real word is never touched
  - the correction must not change the word's length much, so a fragment
    cannot be inflated into a long word it merely starts with

Measured rejection cases this keeps out: "Ilcccpcion" (0.55 against
"reception"), "kn8x", "4r ai". Those stay unread, which is correct.
"""

from __future__ import annotations

from difflib import SequenceMatcher

# Wording that actually appears on signage, shopfronts, menus and packaging.
# Deliberately narrow: every entry is a word the reader is now allowed to
# invent, so breadth here is risk, not coverage.
LEXICON: frozenset[str] = frozenset(
    {
        # wayfinding
        "exit", "entrance", "entry", "reception", "stairs", "elevator",
        "lift", "escalator", "restroom", "toilet", "washroom", "platform",
        "gate", "departures", "arrivals", "baggage", "claim", "information",
        "ticket", "office", "parking", "level", "floor", "room", "suite",
        "lobby", "waiting", "emergency", "fire", "hospital", "pharmacy",
        "police", "library", "museum", "station", "airport", "terminal",
        # instructions
        "push", "pull", "open", "closed", "stop", "caution", "warning",
        "danger", "private", "staff", "only", "keep", "door", "please",
        "wait", "here", "quiet", "zone", "smoking", "wet", "slippery",
        "authorized", "personnel", "occupied", "vacant", "out", "order",
        # shops and food
        "cafe", "coffee", "bakery", "bar", "grill", "kitchen", "market",
        "grocery", "pharmacy", "salon", "hotel", "menu", "special",
        "fresh", "organic", "natural", "original", "classic", "premium",
        # packaging
        "sugar", "free", "diet", "zero", "light", "sodium", "calories",
        "protein", "vitamin", "ingredients", "contains", "allergy",
        "advice", "nuts", "milk", "gluten", "wheat", "soy", "water",
        "juice", "cola", "soda", "tea", "lemon", "lime", "orange",
        "apple", "grape", "berry", "vanilla", "chocolate", "caffeine",
        "sparkling", "still", "whole", "cream", "yogurt", "cheese",
        "bread", "butter", "almond", "peanut", "energy", "drink",
        "ginger", "ale", "tonic", "beer", "wine", "juice",
        "best", "before", "expires", "refrigerate", "shake", "well",
        "recycle", "recyclable", "net", "weight", "volume", "serving",
    }
)

# One wrong or missing character in a short word scores ~0.857 ("Roo" vs
# "room", "ire" vs "fire"), which is exactly the cursive failure this
# exists for. Unrecoverable garbage sits far below -- "Ilcccpcion" against
# "reception" is 0.55 -- so the gap is wide and 0.85 sits inside it.
_SNAP_THRESHOLD = 0.85
# A correction may not change length by more than this, so "ion" cannot
# become "information".
_MAX_LENGTH_RATIO = 0.34
_MIN_TOKEN_CHARS = 3


def _best_match(token: str) -> tuple[str, float] | None:
    lowered = token.lower()
    best, best_ratio = None, 0.0
    for word in LEXICON:
        if abs(len(word) - len(lowered)) / max(len(word), len(lowered)) > _MAX_LENGTH_RATIO:
            continue
        ratio = SequenceMatcher(None, lowered, word).ratio()
        if ratio > best_ratio:
            best, best_ratio = word, ratio
    return (best, best_ratio) if best else None


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement.capitalize()
    return replacement


def correct_token(token: str) -> str:
    """Snap a near-miss to a known word, or return it unchanged."""
    stripped = token.strip()
    if len(stripped) < _MIN_TOKEN_CHARS:
        return token
    # Mostly-alphabetic covers digit substitutions, which OCR makes
    # constantly: "Recepti0n", "Stair5". A token that is mostly digits is
    # a room or platform number and must never be snapped to a word.
    letters = sum(ch.isalpha() for ch in stripped)
    if letters / len(stripped) < 0.7:
        return token
    if stripped.lower() in LEXICON:
        return token  # already a real word; never second-guess it

    match = _best_match(stripped)
    if match and match[1] >= _SNAP_THRESHOLD:
        return _match_case(stripped, match[0])
    return token


def correct_text(text: str) -> str:
    """Apply near-miss correction token by token."""
    return " ".join(correct_token(t) for t in text.split(" "))
