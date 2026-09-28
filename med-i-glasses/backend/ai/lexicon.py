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


# Words that only make sense on a medication label. A sign reading "TABLES"
# was spoken as "TABLETS" during a presentation: "tables" is one edit from
# "tablets", and every lexicon word was a candidate in every context. Domain
# vocabulary has to be gated on the domain, or the lexicon is just overfitting
# with extra steps.
MEDICAL_ONLY: frozenset[str] = frozenset(
    {
        "tablet", "tablets", "capsule", "capsules", "dose", "doses", "dosage",
        "refill", "refills", "prescription", "pharmacy", "pharmacist",
        "milligram", "milligrams", "millilitre", "milliliter",
    }
)

# Reachable from any text: wayfinding, instructions, shops, packaging.
GENERAL_LEXICON: frozenset[str] = LEXICON - MEDICAL_ONLY

# Ordinary words that sit one edit from something in the lexicon. Without
# these, a real word gets "corrected" into a lexicon word: tables -> tablets,
# chairs -> chair(s) collapsing, cables -> tables. The system dictionary in
# _dictionary_words() catches most of this where it exists, but it is macOS
# only and thin on plurals, so the collisions we have actually seen are
# listed explicitly and work everywhere.
PROTECTED: frozenset[str] = frozenset(
    {
        "table", "tables", "cable", "cables", "stable", "stables",
        "chair", "chairs", "door", "doors", "floor", "floors",
        "label", "labels", "able", "fable", "gable", "sable",
        "cabler", "tabled", "stapler", "staple", "staples",
        "capsul",  # a real OCR fragment, but not evidence of a capsule
        "doze", "dozen", "hose", "nose", "rose", "close", "chose",
        "refile", "profile", "reface",
    }
)


def _dictionary_words() -> frozenset[str]:
    """The system word list, where there is one.

    A real English word must never be rewritten into a lexicon word. This is
    best-effort: the file is macOS/BSD only and light on plurals, so it
    supplements PROTECTED rather than replacing it.
    """
    from pathlib import Path

    for candidate in (Path("/usr/share/dict/words"), Path("/usr/dict/words")):
        try:
            if candidate.exists():
                return frozenset(
                    w.strip().lower() for w in candidate.read_text(
                        encoding="utf-8", errors="ignore"
                    ).splitlines() if w.strip()
                )
        except Exception:
            break
    return frozenset()


_DICTIONARY = _dictionary_words()


# The dictionary veto applies only from this length up. web2 is a 1934
# Webster's with 234k entries, so it contains plenty of obscure words that in
# practice are OCR fragments: "ire" and "inger" are both in it, but a reader
# meeting them is looking at "Fire" and "ginger" with the cursive lead-in
# capital lost. Every collision that actually cost us was a six-letter-plus
# plural noun (tables, chairs, cables, stables); every repair that matters is
# a short fragment. PROTECTED is curated and applies at any length.
_DICTIONARY_VETO_CHARS = 6


def is_known_word(token: str) -> bool:
    """Is this word one the lexicon recognises, allowing a plural?

    Membership, not correction. This used to be answered by running the
    corrector and checking whether its output landed in the lexicon, which
    tied a confidence measure to whatever the corrector happened to be
    willing to rewrite -- so tightening the corrector silently lowered
    confidence on lines that were perfectly readable ("CONTAINS PEANUTS").
    """
    lowered = "".join(ch for ch in token if ch.isalpha()).lower()
    if not lowered:
        return False
    if lowered in LEXICON:
        return True
    for suffix in ("s", "es"):
        if lowered.endswith(suffix) and lowered[: -len(suffix)] in LEXICON:
            return True
    return False


def _is_real_word(token: str) -> bool:
    """Is this already an English word, and so not a misreading to repair?"""
    lowered = token.lower()
    if lowered in PROTECTED:
        return True
    if len(lowered) < _DICTIONARY_VETO_CHARS:
        return False
    if lowered in _DICTIONARY:
        return True
    # web2 carries singulars far more reliably than plurals.
    if lowered.endswith("s") and lowered[:-1] in _DICTIONARY:
        return True
    if lowered.endswith("es") and lowered[:-2] in _DICTIONARY:
        return True
    return False


def _best_match(token: str, medical_context: bool = False) -> tuple[str, float] | None:
    allowed = LEXICON if medical_context else GENERAL_LEXICON
    ranked = [m for m in _ranked_matches(token) if m[0] in allowed]
    return ranked[0] if ranked else None


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement.capitalize()
    return replacement


def correct_token(token: str, medical_context: bool = False) -> str:
    """Snap a near-miss to a known word, or return it unchanged.

    `medical_context` opens the medication vocabulary. Without it a sign
    reading "TABLES" becomes "TABLETS", which is what happened live.
    """
    stripped = token.strip()
    # Mostly-alphabetic covers digit substitutions, which OCR makes
    # constantly: "Recepti0n", "Stair5". A token that is mostly digits is
    # a room or platform number and must never be snapped to a word.
    if not _is_wordlike(stripped):
        return token
    if stripped.lower() in LEXICON:
        return token  # already a known word; never second-guess it
    if _is_real_word(stripped):
        # An ordinary English word is not a misreading. "tables" is a word,
        # so it is never evidence of "tablets".
        return token

    match = _best_match(stripped, medical_context)
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


def _context_snap(token: str, medical_context: bool = False) -> str:
    """The wider snap, allowed only when the line around it is known words.

    The looser threshold makes the real-word veto matter more here, not less:
    at 0.75 a great many ordinary words sit within range of a lexicon entry.
    """
    stripped = token.strip()
    if len(stripped) < _CONTEXT_MIN_CHARS or not _is_wordlike(stripped) or _is_known(stripped):
        return token
    if _is_real_word(stripped):
        return token
    allowed = LEXICON if medical_context else GENERAL_LEXICON
    ranked = [m for m in _ranked_matches(stripped) if m[0] in allowed]
    if not ranked or ranked[0][1] < _CONTEXT_SNAP_THRESHOLD:
        return token
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < _CONTEXT_MARGIN:
        return token  # two words fit; guessing between them is inventing
    return _match_case(stripped, ranked[0][0])


def correct_text(text: str, medical_context: bool = False) -> str:
    """Near-miss correction for a line, using the line as its own context.

    First glued words come apart and each token gets the strict snap. Then,
    if the line now holds at least one known word, the words still unknown
    get one more, slightly wider, try: the rest of the line is the evidence
    that this is prose, and "TAKE 1 TABLET BY MOUIH DAILV" finishes as the
    directions it is. A line with no known word in it gets no such benefit.

    `medical_context` should describe the whole reading, not this line: the
    dose line on a bottle often carries no medical word of its own, while the
    label around it does. Passing it per line would leave that line ungated
    and a shop sign gated by one stray word.
    """
    out: list[str] = []
    for token in text.split(" "):
        parts = split_glued(token) if token.strip() else None
        if parts:
            out.extend(parts)
        else:
            out.append(_tidy_case(correct_token(token, medical_context)))

    if any(_is_known(token) for token in out):
        out = [
            token if _is_known(token) else _context_snap(token, medical_context)
            for token in out
        ]
    return " ".join(out)
