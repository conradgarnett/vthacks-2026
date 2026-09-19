"""Spoken grammar.

Detector class names are not English. Naive formatting produced "a glasses at
your twelve o'clock" on a real frame, which costs credibility immediately -- a
user who hears the assistant speak badly trusts what it says less.
"""

from __future__ import annotations

import pytest

from backend.scene.model import SceneModel
from backend.scene.queries import summarize
from backend.speech.phrasing import article_for, join_spoken, pluralize, with_article
from backend.tests.test_scene import build_scene, make_detection


class TestArticles:
    @pytest.mark.parametrize("label", ["chair", "person", "couch", "door", "table"])
    def test_consonant_labels_take_a(self, label):
        assert article_for(label) == "a"

    @pytest.mark.parametrize("label", ["elevator", "exit sign"])
    def test_irregular_labels_use_their_explicit_article(self, label):
        assert article_for(label) == "an"

    @pytest.mark.parametrize("label", ["stairs", "glasses", "keys", "staircase"])
    def test_plural_and_uncountable_labels_take_no_article(self, label):
        """Regression: real output said 'a glasses'."""
        assert article_for(label) == ""
        assert with_article(label) == label

    def test_with_article_prefixes_countable_nouns(self):
        assert with_article("chair") == "a chair"
        assert with_article("elevator") == "an elevator"

    def test_is_case_insensitive(self):
        assert article_for("Chair") == "a"


class TestJoining:
    @pytest.mark.parametrize(
        "items,expected",
        [
            ([], ""),
            (["a chair"], "a chair"),
            (["a chair", "a door"], "a chair and a door"),
            (["a", "b", "c"], "a, b, and c"),
        ],
    )
    def test_spoken_lists(self, items, expected):
        assert join_spoken(items) == expected


class TestPluralize:
    def test_singular_and_regular_plural(self):
        assert pluralize(1, "step") == "1 step"
        assert pluralize(3, "step") == "3 steps"

    @pytest.mark.parametrize(
        "count,singular,expected",
        [
            (2, "person", "2 people"),
            (2, "box", "2 boxes"),
            (2, "bus", "2 buses"),
            (2, "couch", "2 couches"),
        ],
    )
    def test_irregular_plurals(self, count, singular, expected):
        """'2 persons' and '2 boxs' both sound like a machine talking."""
        assert pluralize(count, singular) == expected

    def test_already_plural_nouns_are_not_doubled(self):
        assert pluralize(2, "stairs") == "2 stairs"


class TestSummaryGrammar:
    def test_summary_never_says_a_before_a_plural_noun(self):
        scene = build_scene([make_detection(label="stairs", distance=2.0)])
        assert "a stairs" not in summarize(scene).lower()

    def test_repeated_objects_are_counted_not_listed_separately(self):
        """Three clauses each naming a person is harder to act on than
        'three people' -- and it is what the raw detector output produces."""
        scene = build_scene(
            [
                make_detection(label="person", x=60, distance=2.0, azimuth=-20),
                make_detection(label="person", x=260, distance=2.4, azimuth=0),
                make_detection(label="person", x=460, distance=3.1, azimuth=20),
            ]
        )
        spoken = summarize(scene)
        assert "3 people" in spoken, spoken
        assert spoken.lower().count("at your") == 1, spoken

    def test_verb_agrees_with_a_plural_group(self):
        """Regression: real output said 'There's 5 people'."""
        scene = build_scene(
            [
                make_detection(label="person", x=60, distance=2.0, azimuth=-20),
                make_detection(label="person", x=460, distance=2.4, azimuth=20),
            ]
        )
        assert summarize(scene).startswith("There are")

    def test_verb_agrees_with_a_single_object(self):
        scene = build_scene([make_detection(label="chair", distance=2.0)])
        assert summarize(scene).startswith("There's")

    def test_summary_reports_the_nearest_of_a_group(self):
        scene = build_scene(
            [
                make_detection(label="chair", x=60, distance=4.0, azimuth=-20),
                make_detection(label="chair", x=460, distance=1.2, azimuth=20),
            ]
        )
        assert "1.2 meters" in summarize(scene)

    def test_empty_scene_stays_graceful(self):
        assert "can't" in summarize(SceneModel())
