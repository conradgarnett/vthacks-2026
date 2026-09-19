"""Separating real text from OCR noise.

Every garbage string here was produced by Vision on an actual degraded frame,
not invented. The asymmetry driving the thresholds: a blind user cannot glance
at the sign to check, so speaking gibberish is misinformation, while staying
quiet is merely unhelpful.
"""

from __future__ import annotations

import pytest

from backend.ai.text_quality import assess, clean_for_speech, is_plausible, normalize


class TestRejectsNoise:
    @pytest.mark.parametrize(
        "garbage",
        [
            "J¥Y.A'¢&'.;",          # observed on a carpet texture
            "-01?� It.:.:",    # observed on foliage
            ".,;:",
            "|||",
            "~~~~",
            "���",
        ],
    )
    def test_symbol_soup_is_rejected(self, garbage):
        assert not is_plausible(garbage)

    def test_consonant_runs_are_rejected(self):
        """No language using this alphabet has vowelless words this long."""
        assert not is_plausible("bcdfghjk")
        assert not is_plausible("xkcdwwmn")

    def test_single_characters_are_rejected(self):
        assert not is_plausible("x")
        assert not is_plausible(".")

    def test_high_confidence_does_not_rescue_garbage(self):
        """Vision reports ~0.5 for almost everything, garbage included."""
        assert not is_plausible("J¥Y.A'¢&'.;", confidence=0.99)


class TestKeepsRealText:
    @pytest.mark.parametrize(
        "text",
        [
            "EXIT",
            "Keep door closed",
            "Room 204B",
            "Platform 9",
            "Conference Room B",
            "Capacity 24",
            "No entry",
            "WC",
            "Push",
        ],
    )
    def test_signage_is_kept(self, text):
        assert is_plausible(text), f"rejected real signage: {text}"

    @pytest.mark.parametrize(
        "text", ["EXIT,,", "Conference Room B .", "Platform 9 , .,", "Room 204B."]
    )
    def test_stray_punctuation_does_not_condemn_a_good_read(self, text):
        """Regression: OCR appends stray marks to text it read correctly, and
        counting them as evidence of gibberish discarded perfect reads."""
        assert is_plausible(text), f"punctuation noise rejected: {text}"

    def test_room_codes_survive_despite_having_no_vowels(self):
        assert is_plausible("B12")
        assert is_plausible("A-4")


class TestCleanForSpeech:
    def test_strips_stray_marks_a_voice_would_read_aloud(self):
        """A speech engine voices these literally: 'EXIT comma comma'."""
        assert clean_for_speech("EXIT,,") == "EXIT"
        assert clean_for_speech("Conference Room B .") == "Conference Room B"

    def test_preserves_meaningful_internal_punctuation(self):
        assert clean_for_speech("A-4") == "A-4"
        assert clean_for_speech("don't enter") == "don't enter"

    def test_collapses_whitespace(self):
        assert clean_for_speech("  EXIT   here  ") == "EXIT here"


class TestScoring:
    def test_clean_text_scores_above_messy_text(self):
        clean = assess("Keep door closed", 0.5).score
        messy = assess("Keep dbbr clsd ,, ;", 0.5).score
        assert clean > messy

    def test_reason_is_recorded_for_debugging(self):
        assert assess(".,;:", 0.5).reason


class TestNormalize:
    def test_ignores_case_and_punctuation_for_comparison(self):
        assert normalize("Room 204B") == normalize("room, 204b.")

    def test_distinguishes_genuinely_different_text(self):
        assert normalize("EXIT") != normalize("ENTER")
