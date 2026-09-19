"""Sentence chunking decides when speech starts, so it gets real tests.

A false split makes the assistant pause mid-thought; a missed split delays
speech until the whole answer exists. Both are latency bugs.
"""

from __future__ import annotations

import pytest

from backend.speech.chunker import SentenceChunker


def test_emits_sentence_as_soon_as_it_completes():
    chunker = SentenceChunker()
    assert chunker.feed("A chair is ahead") == []
    assert chunker.feed(". ") == ["A chair is ahead."]


def test_multiple_sentences_in_one_delta():
    chunker = SentenceChunker()
    out = chunker.feed("Door on your left. Table ahead. ")
    assert out == ["Door on your left.", "Table ahead."]


@pytest.mark.parametrize("text", ["about 3.5 meters", "the yolo11n.pt file"])
def test_decimal_and_filename_periods_do_not_split(text):
    """A period followed by a non-space is never a boundary."""
    chunker = SentenceChunker()
    assert chunker.feed(text) == []


def test_abbreviation_does_not_split():
    chunker = SentenceChunker()
    assert chunker.feed("Approx. three meters ahead") == []


def test_question_and_exclamation_are_boundaries():
    chunker = SentenceChunker()
    assert chunker.feed("Is the path clear? ") == ["Is the path clear?"]
    assert chunker.feed("Stop! ") == ["Stop!"]


def test_long_clause_flushes_rather_than_stalling_speech():
    """Without a terminator, speech would otherwise never start."""
    chunker = SentenceChunker(max_buffer=40)
    out = chunker.feed("a couch and a lamp and a rug and a table and a chair and more")
    assert out, "expected a forced flush"
    assert all(" " in seg for seg in out)


def test_flush_returns_trailing_text_once():
    chunker = SentenceChunker()
    chunker.feed("Door ahead")
    assert chunker.flush() == "Door ahead"
    assert chunker.flush() is None


def test_streamed_token_by_token_matches_whole_input():
    """Chunking must not depend on how the stream happens to be split."""
    sentence = "Door on your left. Table ahead. "
    chunker = SentenceChunker()
    out: list[str] = []
    for ch in sentence:
        out.extend(chunker.feed(ch))
    assert out == ["Door on your left.", "Table ahead."]
