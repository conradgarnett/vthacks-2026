"""Near-miss correction against a signage and packaging vocabulary.

Cursive faces lose the lead-in capital, leaving a word one edit from correct.
The correction is deliberately narrow: every word in the lexicon is a word the
reader is now allowed to invent, so the tests below care more about what it
REFUSES to touch than about what it fixes.
"""

from __future__ import annotations

import pytest

from backend.ai.lexicon import LEXICON, correct_text, correct_token


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
