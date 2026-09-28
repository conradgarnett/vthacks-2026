"""Near-miss correction against a signage and packaging vocabulary.

Cursive faces lose the lead-in capital, leaving a word one edit from correct.
The correction is deliberately narrow: every word in the lexicon is a word the
reader is now allowed to invent, so the tests below care more about what it
REFUSES to touch than about what it fixes.
"""

from __future__ import annotations

import pytest

from backend.ai.lexicon import LEXICON, correct_text, correct_token, split_glued


class TestFixesNearMisses:
    @pytest.mark.parametrize(
        "garbled,expected",
        [
            ("eception", "reception"),   # cursive drops the lead-in capital
            ("Roo", "Room"),
            ("ire", "Fire"),
            ("inger", "ginger"),
            ("Exi", "Exit"),
            ("Recepti0n", "Reception"),  # digit substitution
            ("Departure5", "Departures"),
        ],
    )
    def test_one_character_from_correct_is_repaired(self, garbled, expected):
        assert correct_token(garbled).lower() == expected.lower()

    def test_case_follows_the_original(self):
        assert correct_token("EXI") == "EXIT"
        assert correct_token("Roo") == "Room"


class TestRefusesToGuess:
    @pytest.mark.parametrize(
        "garbage",
        [
            "Ilcccpcion",  # 0.55 against "reception" -- unrecoverable
            "kn8x",
            "xyzzy",
            "Zapfino",
            "qqqq",
        ],
    )
    def test_unrecoverable_text_is_left_alone(self, garbage):
        """Inventing a plausible word is worse than leaving it unread: the
        user cannot glance at the label to catch it."""
        assert correct_token(garbage) == garbage

    @pytest.mark.parametrize("code", ["204B", "B12", "A-4", "24"])
    def test_room_and_platform_codes_are_never_snapped_to_words(self, code):
        assert correct_token(code) == code

    @pytest.mark.parametrize("word", ["Exit", "Room", "Fire", "Reception", "Cola"])
    def test_a_word_already_correct_is_never_second_guessed(self, word):
        assert correct_token(word) == word

    def test_a_short_fragment_is_not_inflated_into_a_long_word(self):
        """"ion" must not become "information" -- length guard."""
        assert correct_token("ion") == "ion"

    def test_only_words_in_the_lexicon_can_be_produced(self):
        produced = correct_token("eception").lower()
        assert produced in LEXICON


class TestWholeText:
    def test_corrects_token_by_token_and_keeps_the_rest(self):
        assert correct_text("Roo 204B") == "Room 204B"

    def test_leaves_clean_text_untouched(self):
        assert correct_text("Diet Cola") == "Diet Cola"


class TestContext:
    """The line is its own context. Words the reader could not finish are
    repaired from the legible words around them, never in isolation."""

    def test_directions_finish_when_the_rest_of_the_line_is_known(self):
        assert correct_text("TAKE 1 TABLET BY MOUIH DAILV") == "TAKE 1 TABLET BY MOUTH DAILY"

    def test_a_lone_garbled_word_gets_no_wider_snap(self):
        """No known word beside it, no evidence it is prose: left as read."""
        assert correct_text("MOUIH") == "MOUIH"

    def test_unrecoverable_garble_stays_garbled_even_in_context(self):
        assert correct_text("Ilcccpcion desk") == "Ilcccpcion desk"

    def test_codes_are_never_snapped_even_in_context(self):
        assert correct_text("Room 204B") == "Room 204B"
        assert correct_text("Rx6233425 daily") == "Rx6233425 daily"

    def test_scrambled_case_of_a_known_word_is_tidied(self):
        assert correct_text("TAKE 2 taBLEtS BY MoUTh") == "TAKE 2 TABLETS BY MOUTH"

    def test_title_case_is_left_alone(self):
        assert correct_text("Fire Exit") == "Fire Exit"


class TestGluedWords:
    @pytest.mark.parametrize(
        "glued, apart",
        [
            ("DISCARDAFTER", ["DISCARD", "AFTER"]),
            ("GINGERALE", ["GINGER", "ALE"]),
            ("DIETCOLA", ["DIET", "COLA"]),
            ("SparklingWater", ["Sparkling", "Water"]),
        ],
    )
    def test_a_run_of_known_words_comes_apart(self, glued, apart):
        assert split_glued(glued) == apart

    def test_a_real_word_is_never_split(self):
        assert split_glued("INFORMATION") is None

    def test_a_run_with_an_unknown_piece_is_not_split(self):
        assert split_glued("CARBONATEDWATER") is None

    def test_split_words_are_then_spoken_apart(self):
        assert correct_text("DISCARDAFTER 09/28") == "DISCARD AFTER 09/28"


class TestDomainGating:
    """Regression: a sign reading "TABLES" was spoken as "TABLETS" in a demo.

    The lexicon was medication vocabulary applied to every context. "tables"
    is one edit from "tablets", so a shop sign inherited a pill bottle's
    words. Domain vocabulary has to be gated on the domain.
    """

    @pytest.mark.parametrize(
        "word", ["TABLES", "Tables", "tables", "table", "stables", "cables", "labels"]
    )
    def test_ordinary_words_survive_outside_a_medical_context(self, word):
        assert correct_text(word) == word

    def test_the_reported_sign_reads_correctly(self):
        assert correct_text("CONFERENCE TABLES") == "CONFERENCE TABLES"

    def test_medication_words_are_unreachable_from_a_sign(self):
        from backend.ai.lexicon import GENERAL_LEXICON, MEDICAL_ONLY

        assert "tablets" in MEDICAL_ONLY
        assert not (MEDICAL_ONLY & GENERAL_LEXICON), "medical words leaked into general"

    def test_medication_words_still_repair_on_a_label(self):
        """Gating must not cost the case the lexicon exists for."""
        assert correct_text("CAPSUIE", medical_context=True) == "CAPSULE"
        assert correct_text("REFILS", medical_context=True) == "REFILLS"

    def test_a_real_word_is_never_rewritten_even_on_a_label(self):
        """"TABLE" on a pill bottle is still the word table. Protecting a real
        word costs a rare repair; rewriting one invents content."""
        assert correct_text("TABLES", medical_context=True) == "TABLES"


class TestRealWordVeto:
    def test_a_dictionary_word_is_not_treated_as_a_misreading(self):
        from backend.ai.lexicon import _is_real_word

        for word in ("table", "tables", "close", "rose", "profile"):
            assert _is_real_word(word), word

    def test_a_garbled_token_is_not_protected(self):
        from backend.ai.lexicon import _is_real_word

        for junk in ("CAPSUIE", "REFILS", "eception", "Recepti0n"):
            assert not _is_real_word(junk), junk
