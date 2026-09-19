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

# Wording on prescription directions and warning stickers. The medication
# guard governs the numbers; these are the words around them, and a label
# read as "TAKE 1 TABLET BY MOUIH DAILV" is one the reader could otherwise
# not finish. Every entry is still a word the reader may now invent, so
# this stays to what a pharmacy actually prints.
_MEDICATION_WORDS: frozenset[str] = frozenset(
    {
        "take", "tablet", "tablets", "capsule", "capsules", "mouth",
        "daily", "twice", "once", "three", "times", "every", "hour",
        "hours", "morning", "evening", "bedtime", "night", "food",
        "with", "without", "refill", "refills", "discard", "after",
        "prescription", "medication", "medicine", "dose", "avoid",
        "sunlight", "alcohol", "drowsiness", "cause", "finish", "chew",
        "swallow", "crush", "store", "children", "reach", "drugs",
        "chemist", "city", "university", "patient", "doctor", "physician",
        "pharmacist", "directions", "quantity", "expires", "date",
    }
)
LEXICON = LEXICON | _MEDICATION_WORDS

# One wrong or missing character in a short word scores ~0.857 ("Roo" vs
# "room", "ire" vs "fire"), which is exactly the cursive failure this
# exists for. Unrecoverable garbage sits far below -- "Ilcccpcion" against
# "reception" is 0.55 -- so the gap is wide and 0.85 sits inside it.
_SNAP_THRESHOLD = 0.85
# A correction may not change length by more than this, so "ion" cannot
# become "information".
_MAX_LENGTH_RATIO = 0.34
_MIN_TOKEN_CHARS = 3

# With the rest of the line already made of known words, a longer token may
# snap from a little further away: one wrong character in a five-letter
# word ("MOUIH" against "mouth" is 0.80), two in a word of eight or more.
# The line is the evidence that this is prose and not a code, and the
# candidate has to be unique, with the runner-up clearly behind. Without
# that context a lone "MOUIH" stays as it was read.
_CONTEXT_SNAP_THRESHOLD = 0.75
_CONTEXT_MIN_CHARS = 5
_CONTEXT_MARGIN = 0.08
# Glued words: a run of letters that is not a word but is several known
# words in a row. Bold capitals lose their spaces on some engines
# ("DISCARDAFTER", "GINGERALE"). Only whole known words, each of three
# letters or more, and never more than three of them.
_GLUE_MIN_CHARS = 7
_GLUE_MIN_PART = 3
_GLUE_MAX_PARTS = 3


def _ranked_matches(token: str) -> list[tuple[str, float]]:
    """Known words by similarity, best first, length-compatible ones only."""
    lowered = token.lower()
    ranked: list[tuple[str, float]] = []
    for word in LEXICON:
        if abs(len(word) - len(lowered)) / max(len(word), len(lowered)) > _MAX_LENGTH_RATIO:
            continue
        ranked.append((word, SequenceMatcher(None, lowered, word).ratio()))
    ranked.sort(key=lambda pair: -pair[1])
    return ranked


def _best_match(token: str) -> tuple[str, float] | None:
    ranked = _ranked_matches(token)
    return ranked[0] if ranked else None


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement.capitalize()
    return replacement


def correct_token(token: str) -> str:
    """Snap a near-miss to a known word, or return it unchanged."""
    stripped = token.strip()
    # Mostly-alphabetic covers digit substitutions, which OCR makes
    # constantly: "Recepti0n", "Stair5". A token that is mostly digits is
    # a room or platform number and must never be snapped to a word.
    if not _is_wordlike(stripped):
        return token
    if stripped.lower() in LEXICON:
        return token  # already a real word; never second-guess it

    match = _best_match(stripped)
    if match and match[1] >= _SNAP_THRESHOLD:
        return _match_case(stripped, match[0])
    return token


def _is_wordlike(token: str) -> bool:
    stripped = token.strip()
    if len(stripped) < _MIN_TOKEN_CHARS:
        return False
    letters = sum(ch.isalpha() for ch in stripped)
    return letters / len(stripped) >= 0.7


def _is_known(token: str) -> bool:
    return token.strip().lower() in LEXICON


def _tidy_case(token: str) -> str:
    """A known word read in scrambled case ("taBLEtS") takes the case most
    of its letters have; a title-case word is left alone."""
    stripped = token.strip()
    if not _is_known(stripped) or stripped.isupper() or stripped.islower() or stripped.istitle():
        return token
    uppers = sum(ch.isupper() for ch in stripped)
    lowers = sum(ch.islower() for ch in stripped)
    return stripped.upper() if uppers >= lowers else stripped.lower()


def split_glued(token: str) -> list[str] | None:
    """Split a run of letters into known words, or None if it is not one."""
    stripped = token.strip()
    lowered = stripped.lower()
    if len(lowered) < _GLUE_MIN_CHARS or not lowered.isalpha() or lowered in LEXICON:
        return None
    # Fewest parts wins; every part must be a whole known word.
    best: dict[int, list[str]] = {0: []}
    for end in range(_GLUE_MIN_PART, len(lowered) + 1):
        for start in range(0, end - _GLUE_MIN_PART + 1):
            if start not in best or lowered[start:end] not in LEXICON:
                continue
            candidate = best[start] + [lowered[start:end]]
            if end not in best or len(candidate) < len(best[end]):
                best[end] = candidate
    parts = best.get(len(lowered))
    if not parts or len(parts) < 2 or len(parts) > _GLUE_MAX_PARTS:
        return None
    return [_match_case(stripped, part) for part in parts]


def _context_snap(token: str) -> str:
    """The wider snap, allowed only when the line around it is known words."""
    stripped = token.strip()
    if len(stripped) < _CONTEXT_MIN_CHARS or not _is_wordlike(stripped) or _is_known(stripped):
        return token
    ranked = _ranked_matches(stripped)
    if not ranked or ranked[0][1] < _CONTEXT_SNAP_THRESHOLD:
        return token
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < _CONTEXT_MARGIN:
        return token  # two words fit; guessing between them is inventing
    return _match_case(stripped, ranked[0][0])


def correct_text(text: str) -> str:
    """Near-miss correction for a line, using the line as its own context.

    First glued words come apart and each token gets the strict snap. Then,
    if the line now holds at least one known word, the words still unknown
    get one more, slightly wider, try: the rest of the line is the evidence
    that this is prose, and "TAKE 1 TABLET BY MOUIH DAILV" finishes as the
    directions it is. A line with no known word in it gets no such benefit.
    """
    out: list[str] = []
    for token in text.split(" "):
        parts = split_glued(token) if token.strip() else None
        if parts:
            out.extend(parts)
        else:
            out.append(_tidy_case(correct_token(token)))

    if any(_is_known(token) for token in out):
        out = [token if _is_known(token) else _context_snap(token) for token in out]
    return " ".join(out)
