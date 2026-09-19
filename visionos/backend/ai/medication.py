"""Medication labels get stricter rules than anything else the reader sees.

Every other failure in this system costs convenience. A misread dose costs
health: "TAKE 2 TABLETS" heard as "take 3" is an overdose instruction, and the
user cannot glance at the bottle to catch it. Measured on a prescription
corpus, the unguarded reader reported a wrong dose on 7% of labels.

So when a label looks medical, a number is only spoken if several frames
independently agree on it. Anything less is reported as unreadable, with an
explicit instruction to get it checked. Refusing to read a dose is a mild
failure; reading it wrong is not, and the two must never be traded off.

This deliberately makes medication labels *worse* on paper -- fewer doses
read -- in exchange for never being confidently wrong about one.
"""

from __future__ import annotations

import re

# Words that mark a label as medical. Two or more must appear, so a shopping
# list mentioning "tablet" does not trigger the stricter path.
_MEDICAL_MARKERS = (
    "tablet", "tablets", "capsule", "capsules", "mg", "mcg", "ml",
    "rx", "refill", "refills", "pharmacy", "prescription", "dose",
    "by mouth", "daily", "twice", "bedtime", "physician", "pharmacist",
    "take", "qty",
)
_MIN_MARKERS = 2

# The quantity in "TAKE 2 TABLETS". Words are included because labels use both.
_DOSE_PATTERN = re.compile(
    r"\btake\s+(\d+|one|two|three|four|half|a\s+half)\b", re.IGNORECASE
)
_ANY_NUMBER = re.compile(r"\d+")

UNREADABLE_DOSE = (
    "This looks like a medication label, but I can't read the dosage clearly "
    "enough to say it safely. Please have someone check it."
)


def looks_medical(text: str) -> bool:
    lowered = text.lower()
    return sum(1 for marker in _MEDICAL_MARKERS if marker in lowered) >= _MIN_MARKERS


def has_dose_instruction(text: str) -> bool:
    return bool(_DOSE_PATTERN.search(text))


def dose_is_corroborated(lines, minimum_agreement: int = 2) -> bool:
    """Did several frames independently produce the same dose line?

    Agreement across frames is the only evidence available that a number was
    read rather than guessed. Blur, glare and compression differ from frame to
    frame, so a misread digit rarely repeats; a real one does.
    """
    for line in lines:
        text = getattr(line, "text", "")
        if not _DOSE_PATTERN.search(text):
            continue
        if getattr(line, "agreement", 1) >= minimum_agreement:
            return True
    return False


def contains_unverified_number(text: str) -> bool:
    """Any digit at all, which on a medical label could be a dose."""
    return bool(_ANY_NUMBER.search(text))


def guard(spoken: str, lines) -> str:
    """Strip unverified numbers from a medication label reading.

    Non-medical text passes through untouched -- this must not make street
    signs or packaging more timid.
    """
    if not looks_medical(spoken):
        return spoken
    if not contains_unverified_number(spoken):
        return spoken
    if dose_is_corroborated(lines):
        return spoken

    # A dose was read but only once, or numbers appear with no dose line to
    # anchor them. Neither is safe to speak.
    return f"{_strip_numbers(spoken)} {UNREADABLE_DOSE}".strip()


def _strip_numbers(text: str) -> str:
    """Keep the words, drop every digit run and the dose phrase around it."""
    without_dose = _DOSE_PATTERN.sub("take an unclear number of", text)
    return re.sub(r"\s+", " ", _ANY_NUMBER.sub("", without_dose)).strip()
