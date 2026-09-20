"""Medication labels: never state a dose we are not sure of.

Every other failure in this system costs convenience. A misread dose costs
health, and the user cannot glance at the bottle to catch it. These tests
exist to make the refusal path hard to remove by accident.
"""

from __future__ import annotations

import pytest

from backend.ai.medication import (
    UNREADABLE_DOSE,
    dose_is_corroborated,
    guard,
    has_dose_instruction,
    looks_medical,
)


class Line:
    def __init__(self, text: str, agreement: int = 1):
        self.text = text
        self.agreement = agreement


class TestDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "TAKE 2 TABLETS BY MOUTH DAILY",
            "AMOXICILLIN 500 MG QTY 30 REFILLS 2",
            "take 1 capsule every 8 hours",
        ],
    )
    def test_recognises_a_medication_label(self, text):
        assert looks_medical(text)

    @pytest.mark.parametrize(
        "text", ["EXIT", "Diet Cola Zero Sugar", "Platform 9 Departures"],
    )
    def test_ordinary_text_is_not_treated_as_medical(self, text):
        """The strict path must not make street signs timid."""
        assert not looks_medical(text)

    def test_one_marker_alone_is_not_enough(self):
        assert not looks_medical("a tablet computer")


class TestDoseGuard:
    def test_a_dose_seen_in_one_frame_only_is_withheld(self):
        """Blur and glare differ per frame, so a misread digit rarely repeats
        and a single sighting is not evidence."""
        lines = [Line("TAKE 3 TABLETS BY MOUTH DAILY", agreement=1)]
        out = guard("It reads: TAKE 3 TABLETS BY MOUTH DAILY.", lines)
        assert "3" not in out
        assert UNREADABLE_DOSE in out

    def test_a_dose_confirmed_across_frames_is_spoken(self):
        lines = [Line("TAKE 2 TABLETS BY MOUTH DAILY", agreement=3)]
        out = guard("It reads: TAKE 2 TABLETS BY MOUTH DAILY.", lines)
        assert "2" in out
        assert UNREADABLE_DOSE not in out

    def test_numbers_with_no_dose_line_to_anchor_them_are_withheld(self):
        """Digits floating in a medical read could be anything -- strength,
        quantity, a misread dose -- and must not be voiced as instructions."""
        lines = [Line("IBUPROF3N 600 MG QTY 30 REFILLS", agreement=1)]
        out = guard("It reads: IBUPROF3N 600 MG QTY 30 REFILLS.", lines)
        assert UNREADABLE_DOSE in out

    def test_a_strength_agreed_across_frames_is_spoken_while_the_dose_is_withheld(self):
        """Numbers several frames agreed on are real; only the dose seen once
        is withheld. Otherwise a fixed-focus webcam leaves the user hearing
        no numbers at all."""
        lines = [
            Line("AMOXICILLIN 500 MG", agreement=3),
            Line("QTY 30 REFILLS 2", agreement=2),
            Line("TAKE 1 TABLET BY MOUTH DAILY", agreement=1),
        ]
        out = guard("It reads: AMOXICILLIN 500 MG. QTY 30 REFILLS 2. TAKE 1 TABLET BY MOUTH DAILY.", lines)
        assert "500 MG" in out and "QTY 30" in out
        assert "TAKE 1" not in out and "take an unclear number of" in out
        assert UNREADABLE_DOSE in out

    def test_a_number_seen_once_is_still_withheld(self):
        lines = [Line("AMOXICILLIN 500 MG", agreement=1), Line("TAKE 1 TABLET DAILY", agreement=1)]
        out = guard("It reads: AMOXICILLIN 500 MG. TAKE 1 TABLET DAILY.", lines)
        assert "500" not in out and "1" not in out

    def test_a_number_inside_an_uncorroborated_dose_line_never_leaks(self):
        """The dose line's own digits are never corroborating evidence for
        themselves, however many frames agreed on the rest."""
        lines = [Line("TAKE 2 TABLETS DAILY", agreement=1), Line("REFILLS 2", agreement=3)]
        out = guard("It reads: TAKE 2 TABLETS DAILY. REFILLS 2.", lines)
        assert "TAKE 2" not in out and "REFILLS 2" in out

    def test_the_refusal_says_what_to_do(self):
        """Silence is indistinguishable from a crash; the user needs to know
        the label was seen and could not be trusted."""
        assert "check" in UNREADABLE_DOSE.lower()

    def test_non_medical_text_passes_through_untouched(self):
        spoken = "It reads: Platform 9. Departures."
        assert guard(spoken, [Line("Platform 9", 1)]) == spoken

    def test_medical_text_without_numbers_is_untouched(self):
        spoken = "It reads: TAKE WITH FOOD. MAY CAUSE DROWSINESS."
        assert guard(spoken, [Line("TAKE WITH FOOD", 1)]) == spoken


class TestDoseParsing:
    @pytest.mark.parametrize(
        "text", ["TAKE 2 TABLETS", "take one capsule", "Take half tablet"]
    )
    def test_finds_a_dose_instruction(self, text):
        assert has_dose_instruction(text)

    def test_frequency_is_not_mistaken_for_a_dose(self):
        """"TAKE 1 TABLET THREE TIMES DAILY" has dose 1 and frequency 3.
        Conflating them scored a correct refusal as a wrong dose."""
        lines = [Line("TAKE 1 TABLET THREE TIMES DAILY", agreement=3)]
        assert dose_is_corroborated(lines)
