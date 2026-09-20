"""What an allergen looks like on a label, and what the camera may call a
food.

The allergy scanner has two kinds of input and treats them differently:

  - text the reader produced ("CONTAINS: MILK, PEANUTS", an ingredients
    list), searched here for the words a label prints for each allergen
    group. This is evidence: a label says what is in the packet.
  - a food class the detector recognized ("cookie", "peanut butter"). This
    is a trigger, never evidence: the allergen is not in the pixels, a
    cookie may or may not contain nuts, and a detector class says nothing
    about a recipe. A food in view means the wearer may be eating and there
    may be a label worth reading; a handful of classes that *are* the
    allergen (peanuts, shrimp, a whole egg) earn a hedged spoken warning,
    and none of them ever earns an email.

Words are matched whole, so "egg" is not found in "veggie" and "nuts" is not
found in "donuts"; and a line that says a thing is absent ("dairy free",
"no nuts", "does not contain milk") is not a mention of it. The word lists
are the ones printed on packaging, not every food that may contain the
allergen: a word here is a word the scanner may email a doctor about, so
breadth is risk, exactly as it is in the lexicon.

Confidence is not judged here. A mention carries the line's own confidence
and how many frames agreed on it; the watch applies the 80% rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from backend.alerts.profile import normalize_allergen

# Allergen group -> the words a label prints for it. Multi-word entries are
# matched as phrases. Dairy words that plants borrow ("almond milk", "peanut
# butter", "coconut yogurt") are handled by _PLANT_BEFORE below.
ALLERGEN_WORDS: dict[str, tuple[str, ...]] = {
    "peanut": ("peanut", "peanuts", "groundnut", "groundnuts", "arachis"),
    "tree nut": (
        "almond", "almonds", "cashew", "cashews", "walnut", "walnuts", "hazelnut", "hazelnuts",
        "pecan", "pecans", "pistachio", "pistachios", "macadamia", "macadamias", "brazil nut",
        "brazil nuts", "tree nut", "tree nuts", "nut", "nuts",
    ),
    "dairy": (
        "milk", "dairy", "lactose", "whey", "casein", "caseinate", "cheese", "yogurt", "yoghurt",
        "buttermilk", "ghee", "butter", "ice cream", "sour cream", "cream cheese", "whipped cream",
        "milk chocolate", "milk powder", "milk solids",
    ),
    "egg": ("egg", "eggs", "albumen", "albumin", "egg white", "egg whites", "egg yolk", "egg yolks"),
    "gluten": ("wheat", "gluten", "barley", "rye", "spelt", "semolina", "durum", "wheat flour"),
    "shellfish": (
        "shellfish", "shrimp", "shrimps", "prawn", "prawns", "crab", "lobster", "crustacean",
        "crustaceans", "crayfish", "scallop", "scallops", "clam", "clams", "mussel", "mussels",
        "oyster", "oysters", "mollusc", "molluscs", "mollusk", "mollusks",
    ),
    "fish": ("fish", "anchovy", "anchovies", "salmon", "tuna", "cod", "haddock", "sardine", "sardines"),
    "soy": ("soy", "soya", "soybean", "soybeans", "tofu", "edamame"),
    "sesame": ("sesame", "tahini", "benne"),
}

# A dairy word with one of these just before it is a plant product: almond
# milk, peanut butter, coconut yogurt, cashew cheese.
_PLANT_BEFORE = frozenset({
    "almond", "peanut", "cashew", "hazelnut", "coconut", "oat", "soy", "soya", "rice", "hemp",
    "pea", "sunflower", "seed", "nut", "cocoa", "shea", "apple", "pumpkin", "plant", "vegan",
})
# How far back a "no", "not", "without" or "free" reaches.
_NEGATION_REACH = 4
_ABSENT_BEFORE = frozenset({"no", "not", "without", "non", "free"})
_STATEMENT_TOKENS = frozenset({"contains", "contain", "containing", "ingredients", "allergens", "allergen"})
_ABSENT_BEFORE_STATEMENT = frozenset({"not", "no", "never", "doesnt", "dont", "without"})

_STATEMENT = re.compile(
    r"\b(contains|may contain|allergens?|allergy (?:advice|information|warning)|ingredients|"
    r"traces? of|made (?:in|on) (?:a |the )?(?:facility|factory|equipment|line))\b",
    re.IGNORECASE,
)
# A warning about what may be there, as opposed to what is: "may contain
# nuts", "traces of milk", "made in a facility that handles peanuts". Worth
# saying to the wearer; never grounds for an email.
_HEDGE = re.compile(
    r"\b(may contain|may also contain|may be present|traces? of|made (?:in|on) (?:a |the )?"
    r"(?:facility|factory|equipment|line)|processed (?:in|on)|manufactured (?:in|on))\b",
    re.IGNORECASE,
)
_FOOD_LABEL = re.compile(
    r"\b(ingredients|nutrition|nutritional|calories|kcal|serving|servings|net (?:wt|weight)|"
    r"contains|allergens?|best before|use by|sugars?|sodium|protein|carbohydrates?|fat)\b",
    re.IGNORECASE,
)
_FOOD_LABEL_MIN_HITS = 2
_CLAUSE_SPLIT = re.compile(r"[.;!?]+")
# Digits are part of a word here: a custom allergen can be a dye ("yellow
# 6", "e110"), and a label prints them as such.
_TOKEN = re.compile(r"[a-z0-9]+")

# Classes the detector may know that are food: the trigger for "the wearer
# may be eating". Proposed for the vocabulary and measured before they land
# there; a class absent from the vocabulary never fires.
FOOD_CLASSES: frozenset[str] = frozenset({
    "sandwich", "pizza", "hot dog", "hamburger", "donut", "cake", "cookie", "bread", "bagel",
    "pasta", "cereal", "cheese", "ice cream", "yogurt", "milk carton", "egg", "peanut butter",
    "peanuts", "nuts", "chocolate", "shrimp", "sushi", "orange", "broccoli", "carrot",
    "apple", "banana",
})
# The few foods that are the allergen itself rather than a recipe that may
# hold it. A sure sighting of one earns a hedged spoken warning; never an
# email, because it is still a picture.
OBVIOUS_ALLERGENS: dict[str, tuple[str, ...]] = {
    "peanut butter": ("peanut",),
    "peanuts": ("peanut",),
    "nuts": ("tree nut", "peanut"),
    "shrimp": ("shellfish",),
    "egg": ("egg",),
}

# How each group is spoken.
_SPOKEN: dict[str, str] = {
    "peanut": "peanuts", "tree nut": "tree nuts", "dairy": "dairy", "egg": "egg", "gluten": "gluten",
    "shellfish": "shellfish", "fish": "fish", "soy": "soy", "sesame": "sesame",
}


def spoken_name(allergen: str) -> str:
    return _SPOKEN.get(allergen, allergen)


def is_food(label: str) -> bool:
    return label in FOOD_CLASSES


def obvious_allergens(label: str) -> tuple[str, ...]:
    return OBVIOUS_ALLERGENS.get(label, ())


@dataclass(frozen=True)
class Mention:
    """An allergen word found in a line of text."""

    allergen: str  # the group, e.g. "peanut"
    word: str  # the word or phrase matched, e.g. "peanuts"
    text: str  # the line it sat in
    confidence: float  # the line's confidence as the engine scored it
    agreement: int  # frames that agreed on the line
    statement: bool  # in a CONTAINS / MAY CONTAIN / ALLERGENS / INGREDIENTS line
    hedged: bool = False  # "may contain", "traces of": a warning, not a presence


def _tokens(clause: str) -> list[str]:
    """Lowercase words. Hyphens and slashes split ("nut-free", "milk/soy");
    a "free" glued onto a word comes apart ("dairyfree"); and a run of bold
    capitals the recognizer glued together comes apart the way speech says
    it ("PEANUTBUTTER"), through the lexicon's splitter, which only ever
    produces words it knows. Nothing else of the lexicon is applied here:
    its near-miss snapping turned "MAY CONTAIN" into "MAY CONTAINS" and
    dropped the comma in "PEANUTS, MILK", both of which change the meaning."""
    from backend.ai.lexicon import split_glued

    out: list[str] = []
    for token in _TOKEN.findall(clause.lower().replace("-", " ").replace("/", " ")):
        if len(token) > 6 and token.endswith("free"):
            out.extend([token[:-4], "free"])
            continue
        parts = split_glued(token) if len(token) >= 7 else None
        if parts:
            out.extend(part.lower() for part in parts)
        else:
            out.append(token)
    return out


def _absent(tokens: list[str], start: int, end: int) -> bool:
    """Does the clause say the thing at tokens[start:end] is absent?

    "nut free", "no nuts", "without milk", "free from eggs", "does not
    contain milk" and "contains no milk" are absences. "gluten free,
    contains milk" keeps its milk: a statement word between the absence and
    the allergen stops the absence reaching it, unless that statement is
    itself denied ("does not contain").
    """
    if tokens[end : end + 1] == ["free"]:
        return True
    for back in range(start - 1, max(-1, start - 1 - _NEGATION_REACH), -1):
        word = tokens[back]
        if word in _ABSENT_BEFORE:
            return True
        if word in _STATEMENT_TOKENS:
            return back > 0 and tokens[back - 1] in _ABSENT_BEFORE_STATEMENT
    return False


def _phrases_for(allergen: str) -> list[tuple[str, ...]]:
    words = ALLERGEN_WORDS.get(allergen)
    if words is None:
        # A custom allergen ("kiwi"): the words as typed, and their plural.
        base = tuple(allergen.split())
        words = (allergen, allergen + "s") if base else ()
    phrases = [tuple(w.split()) for w in words]
    # Longest first, so "egg white" wins over "egg" at the same spot.
    return sorted(phrases, key=len, reverse=True)


def _find(tokens: list[str], allergen: str) -> str | None:
    """The first phrase of the allergen present and not said to be absent."""
    for phrase in _phrases_for(allergen):
        size = len(phrase)
        for index in range(0, len(tokens) - size + 1):
            if tokens[index : index + size] != list(phrase):
                continue
            if allergen == "dairy" and index > 0 and tokens[index - 1] in _PLANT_BEFORE:
                continue
            if _absent(tokens, index, index + size):
                continue
            return " ".join(phrase)
    return None


def find_allergen_mentions(lines: Iterable, allergens: Iterable[str]) -> list[Mention]:
    """Every line that names one of the allergens, once per allergen per line.

    `lines` are TextLines, or anything with `text`, `confidence` and
    `agreement`; `allergens` are profile names in any spelling.
    """
    wanted: list[str] = []
    for name in allergens:
        group = normalize_allergen(str(name))
        if group and group not in wanted:
            wanted.append(group)
    if not wanted:
        return []
    mentions: list[Mention] = []
    for line in lines:
        text = str(getattr(line, "text", "") or "").strip()
        if not text:
            continue
        confidence = float(getattr(line, "confidence", 0.0) or 0.0)
        agreement = int(getattr(line, "agreement", 1) or 1)
        statement = bool(_STATEMENT.search(text))
        found: set[str] = set()
        for clause in _CLAUSE_SPLIT.split(text):
            tokens = _tokens(clause)
            if not tokens:
                continue
            hedged = bool(_HEDGE.search(clause))
            for group in wanted:
                if group in found:
                    continue
                word = _find(tokens, group)
                if word is not None:
                    found.add(group)
                    mentions.append(Mention(group, word, text, confidence, agreement, statement, hedged))
    return mentions


def has_statement(lines: Iterable) -> bool:
    """Does any line carry a CONTAINS / MAY CONTAIN / ALLERGENS statement?"""
    return any(_STATEMENT.search(str(getattr(line, "text", "") or "")) for line in lines)


def looks_like_food_label(lines: Iterable) -> bool:
    """Packaging, as opposed to a sign: two or more of the words every food
    label prints (ingredients, nutrition, serving, calories...)."""
    text = " ".join(str(getattr(line, "text", "") or "") for line in lines)
    return len(set(m.lower() for m in _FOOD_LABEL.findall(text))) >= _FOOD_LABEL_MIN_HITS
