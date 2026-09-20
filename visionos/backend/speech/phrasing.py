"""Grammar for spoken labels.

Everything here exists because detector class names are not English. "a
glasses at your twelve o'clock" is what you get from naive formatting, and it
costs credibility instantly -- a user who hears the assistant speak badly
trusts what it says less.
"""

from __future__ import annotations

# Nouns that are already plural or uncountable: they take no article.
_NO_ARTICLE = frozenset(
    {"stairs", "staircase", "glasses", "keys", "hallway", "low ceiling"}
)

# Labels whose article is not predictable from the first letter.
_EXPLICIT_ARTICLE = {
    "elevator": "an",
    "exit sign": "an",
}

_VOWELS = "aeiou"


def article_for(label: str) -> str:
    """Return "a", "an", or "" for a spoken label."""
    normalized = label.strip().lower()
    if normalized in _NO_ARTICLE:
        return ""
    if normalized in _EXPLICIT_ARTICLE:
        return _EXPLICIT_ARTICLE[normalized]
    return "an" if normalized[:1] in _VOWELS else "a"


def with_article(label: str) -> str:
    """"chair" -> "a chair"; "stairs" -> "stairs"; "elevator" -> "an elevator"."""
    article = article_for(label)
    return f"{article} {label}" if article else label


def join_spoken(items: list[str]) -> str:
    """Comma list with a spoken "and" before the last item."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# Detector labels are singular English nouns, so naive "+s" covers most of
# them. These are the ones it gets wrong.
_IRREGULAR_PLURALS = {
    "person": "people",
    "shelf": "shelves",
    "bookshelf": "bookshelves",
    "knife": "knives",
    "box": "boxes",
    "mailbox": "mailboxes",
    "bus": "buses",
    "couch": "couches",
    "bench": "benches",
    "toothbrush": "toothbrushes",
    "drinking glass": "drinking glasses",
    "wine glass": "wine glasses",
    "dish": "dishes",
}


def plural_of(singular: str) -> str:
    normalized = singular.strip().lower()
    if normalized in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[normalized]
    # Already plural or uncountable: "three stairs" not "three stairss".
    if normalized in _NO_ARTICLE or normalized.endswith("s"):
        return normalized
    return f"{normalized}s"


def pluralize(count: int, singular: str) -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {plural_of(singular)}"
