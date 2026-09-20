"""What an allergen looks like on a label: whole words, absences ignored,
plant milks not dairy, "may contain" a warning and not a presence."""

from __future__ import annotations

from backend.ai.ocr import TextLine
from backend.alerts.allergy import (
    find_allergen_mentions,
    has_statement,
    is_food,
    looks_like_food_label,
    obvious_allergens,
    spoken_name,
)
from backend.alerts.profile import Profile, normalize_allergen


def line(text: str, confidence: float = 0.9, agreement: int = 2) -> TextLine:
    return TextLine(text=text, confidence=confidence, top=0.3, left=0.1, height=0.05, agreement=agreement)


class TestWords:
    def test_a_contains_line_names_its_allergens(self):
        mentions = find_allergen_mentions([line("CONTAINS: PEANUTS, MILK")], ["peanut", "dairy", "egg"])
        assert {m.allergen for m in mentions} == {"peanut", "dairy"}
        assert all(m.statement and not m.hedged for m in mentions)
        assert {m.word for m in mentions} == {"peanuts", "milk"}

    def test_whole_words_only(self):
        assert find_allergen_mentions([line("VEGGIE DONUTS")], ["egg", "tree nut"]) == []
        assert find_allergen_mentions([line("SHELLFISH")], ["fish"]) == []

    def test_an_absence_is_not_a_presence(self):
        for text in (
            "DAIRY FREE", "NUT-FREE", "NO NUTS", "FREE FROM EGGS", "DOES NOT CONTAIN MILK",
            "CONTAINS NO MILK", "DAIRYFREE", "WITHOUT EGG",
        ):
            assert find_allergen_mentions([line(text)], ["dairy", "tree nut", "egg"]) == [], text

    def test_gluten_free_keeps_its_milk(self):
        mentions = find_allergen_mentions([line("GLUTEN FREE. CONTAINS MILK")], ["gluten", "dairy"])
        assert [m.allergen for m in mentions] == ["dairy"]
        mentions = find_allergen_mentions([line("GLUTEN FREE CONTAINS MILK")], ["gluten", "dairy"])
        assert [m.allergen for m in mentions] == ["dairy"]

    def test_plant_milks_and_nut_butters_are_not_dairy(self):
        for text in ("ALMOND MILK", "PEANUT BUTTER", "COCONUT YOGURT", "OAT MILK", "VEGAN CHEESE"):
            assert find_allergen_mentions([line(text)], ["dairy"]) == [], text

    def test_milk_chocolate_and_butter_are_dairy(self):
        assert find_allergen_mentions([line("MILK CHOCOLATE")], ["dairy"])[0].word == "milk chocolate"
        assert find_allergen_mentions([line("SUGAR, BUTTER, SALT")], ["dairy"])[0].word == "butter"

    def test_may_contain_and_traces_are_hedged(self):
        for text in ("MAY CONTAIN PEANUTS", "MAY CONTAIN TRACES OF PEANUTS", "MADE IN A FACILITY THAT HANDLES PEANUTS"):
            mentions = find_allergen_mentions([line(text)], ["peanut"])
            assert mentions and mentions[0].hedged, text
        assert not find_allergen_mentions([line("CONTAINS PEANUTS")], ["peanut"])[0].hedged

    def test_a_custom_allergen_matches_its_own_word(self):
        mentions = find_allergen_mentions([line("INGREDIENTS: KIWI, SUGAR")], ["Kiwi"])
        assert mentions and mentions[0].allergen == "kiwi"
        assert find_allergen_mentions([line("KIWIS")], ["kiwi"])[0].word == "kiwis"

    def test_the_lines_confidence_and_agreement_ride_along(self):
        mention = find_allergen_mentions([line("CONTAINS EGG", 0.93, 3)], ["egg"])[0]
        assert mention.confidence == 0.93 and mention.agreement == 3

    def test_one_mention_per_allergen_per_line(self):
        assert len(find_allergen_mentions([line("MILK, WHEY, CHEESE")], ["dairy"])) == 1

    def test_no_allergens_means_no_mentions(self):
        assert find_allergen_mentions([line("CONTAINS PEANUTS")], []) == []

    def test_glued_capitals_are_matched_as_speech_would_say_them(self):
        """The recognizer drops the space in bold capitals; the lexicon
        pulls "PEANUTBUTTER" apart, so the matcher sees the peanut."""
        mentions = find_allergen_mentions([line("PEANUTBUTTER")], ["peanut"])
        assert mentions and mentions[0].allergen == "peanut" and mentions[0].text == "PEANUTBUTTER"

    def test_the_lines_the_scanner_depends_on_pass_the_gate_on_both_engines(self):
        """Conrad's gate (dca34f0) judges the parts of a token with punctuation
        glued inside it. These are the lines an alert can hang on, on the
        Vision path (uninformative confidence) and the RapidOCR path; and the
        junk that must still fail on the Vision path."""
        from backend.ai.text_quality import assess

        for text in (
            "CONTAINS:PEANUTS", "ALLERGENS:MILK,SOY", "MAY CONTAIN:PEANUTS",
            "INGREDIENTS:WHEAT FLOUR,WHEY", "12:30", "CONTAINS: PEANUTS",
            "DAIRY-FREE", "MILK/SOY", "NUT-FREE,VEGAN",
        ):
            assert assess(text, 0.5, False).keep, text
            assert assess(text, 0.95, True).keep, text
        for junk in ("JQ,JJ", "Il:Il", "fik,th", "a:b"):
            assert not assess(junk, 0.5, False).keep, junk

    def test_the_reader_restores_the_space_after_a_glued_colon(self):
        from backend.ai.ocr import _split_colon_glue

        assert _split_colon_glue("CONTAINS:PEANUTS") == "CONTAINS: PEANUTS"
        assert _split_colon_glue("ALLERGENS:MILK,SOY") == "ALLERGENS: MILK,SOY"
        assert _split_colon_glue("12:30") == "12:30"
        assert _split_colon_glue("http://example.com") == "http://example.com"
        assert _split_colon_glue("CONTAINS: PEANUTS") == "CONTAINS: PEANUTS"


class TestProfileSpellings:
    def test_spellings_normalize_to_groups(self):
        assert normalize_allergen("Peanuts") == "peanut"
        assert normalize_allergen("Tree-Nuts") == "tree nut"
        assert normalize_allergen("Milk") == "dairy"
        assert normalize_allergen("Shrimp") == "shellfish"
        assert normalize_allergen("Kiwi") == "kiwi"

    def test_a_profile_dedupes_and_drops_blanks(self):
        profile = Profile.from_dict({"allergens": ["Peanuts", "peanut", "", "Eggs", "milk"]})
        assert profile.allergens == ["peanut", "egg", "dairy"]
        assert Profile.from_dict({"allergens": "peanuts, soy"}).allergens == ["peanut", "soy"]


class TestLabels:
    def test_a_food_label_looks_like_one(self):
        assert looks_like_food_label([line("INGREDIENTS: SUGAR, WHEAT FLOUR"), line("CALORIES 120 PER SERVING")])

    def test_a_sign_does_not(self):
        assert not looks_like_food_label([line("EXIT")])
        assert not looks_like_food_label([line("CONTAINS")])

    def test_statements(self):
        assert has_statement([line("CONTAINS SOY")])
        assert not has_statement([line("EXIT THIS WAY")])


class TestFoods:
    def test_food_classes_and_the_obvious_ones(self):
        assert is_food("pizza") and not is_food("chair")
        assert obvious_allergens("peanut butter") == ("peanut",)
        assert obvious_allergens("cookie") == ()

    def test_spoken_names(self):
        assert spoken_name("peanut") == "peanuts"
        assert spoken_name("tree nut") == "tree nuts"
        assert spoken_name("kiwi") == "kiwi"
