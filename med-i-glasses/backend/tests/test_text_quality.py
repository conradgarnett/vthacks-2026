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


class TestStrokeArtifacts:
    """Symbols and drawings resolved into glyphs.

    Found by testing on packaging rather than signage: barcodes, nutrition
    rings and decorative rules are not text, but an OCR engine resolves them
    into predictable characters. Speaking those to someone who cannot see the
    can is inventing an ingredient.
    """

    @pytest.mark.parametrize(
        "artifact,source",
        [
            ("Il", "barcode bars"),
            ("Illl", "barcode bars"),
            ("1111", "barcode bars"),
            ("llll", "railings or blinds"),
            ("|||", "vertical rules"),
            ("00", "nutrition rings"),
            ("0000", "nutrition rings"),
            ("OO", "circular badges"),
            ("---", "table rules"),
        ],
    )
    def test_structure_is_not_spoken_as_text(self, artifact, source):
        assert not is_plausible(artifact), f"{source} would be spoken as {artifact!r}"

    @pytest.mark.parametrize(
        "text", ["B12", "204B", "24", "100", "2 Liter", "Room 101", "Zero Sugar"]
    )
    def test_real_content_survives_the_rule(self, text):
        """Mixing character families is the tell for real content: "100"
        spans strokes and rings, "000" does not."""
        assert is_plausible(text), f"rejected real content: {text}"

    def test_a_single_character_is_not_treated_as_an_artifact(self):
        """One stroke is ambiguous; the rule needs a run to be confident."""
        from backend.ai.text_quality import _is_stroke_artifact

        assert not _is_stroke_artifact("l")
        assert not _is_stroke_artifact("0")


class TestPerEngineGating:
    """An engine whose confidence discriminates should be trusted with it.

    These heuristics exist to reconstruct a signal Apple Vision does not
    provide -- it reports ~0.5 for everything, garbage included. RapidOCR
    does provide one (median 0.94 on real text, 0.51 on the line it invented
    on a symbol), and applying the full linguistic gate to it costs real text.
    """

    def test_a_confident_engine_rejects_its_own_low_scores(self):
        from backend.ai.text_quality import CONFIDENT_ENGINE_FLOOR

        # is_plausible() does not opt in, so the gate must be asked for.
        assert not assess("EXIT", 0.40, confidence_informative=True).keep
        assert is_plausible("EXIT", confidence=0.40)
        assert CONFIDENT_ENGINE_FLOOR > 0.5

    def test_confidence_gating_is_opt_in(self):
        """Vision's flat 0.5 must not be read as a rejection."""
        assert assess("EXIT", 0.5, confidence_informative=False).keep

    def test_a_confident_engine_keeps_unusual_but_real_text(self):
        """The looser linguistic bar: a high-scoring line the heuristics would
        have second-guessed still reaches speech."""
        # NDC is the National Drug Code, on essentially every medicine
        # label, and the vowelless-run rule rejected it.
        assert assess("RX 8830021 NDC", 0.95, confidence_informative=True).keep
        assert not assess("RX 8830021 NDC", 0.95).keep, "Vision path changed"

    def test_a_confident_engine_still_rejects_symbol_artifacts(self):
        """Stroke artifacts are about what the image contains, not how the
        engine scores, so that rule applies to every engine."""
        assert not assess("Illl", 0.95, confidence_informative=True).keep
        assert not assess("0000", 0.95, confidence_informative=True).keep
