"""Text reading.

Reading order carries most of the risk. Engines return lines in no
guaranteed order, and a sign spoken bottom-up or zig-zagged across columns is
worse than one not read at all: the user has no way to tell it was scrambled.

The engine tests run against whichever OCR engine loads on this machine and
skip when none does.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.ai.ocr import (
    NO_TEXT_FOUND,
    TextLine,
    TextReader,
    build_reader,
    clean_lines,
    format_for_speech,
    merge_readings,
    sort_reading_order,
)


def line(
    text: str,
    top: float,
    left: float = 0.0,
    confidence: float = 0.9,
    height: float = 0.0,
) -> TextLine:
    return TextLine(text=text, confidence=confidence, top=top, left=left, height=height)


def texts(lines: list[TextLine]) -> list[str]:
    return [l.text for l in lines]


class TestReadingOrder:
    def test_sorts_top_to_bottom(self):
        scrambled = [line("third", 0.8), line("first", 0.1), line("second", 0.45)]
        assert texts(sort_reading_order(scrambled)) == ["first", "second", "third"]

    def test_reads_left_to_right_within_a_row(self):
        """Two labels side by side are one row, not two."""
        row = [line("right", 0.30, left=0.7), line("left", 0.31, left=0.1)]
        assert texts(sort_reading_order(row)) == ["left", "right"]

    def test_does_not_zigzag_across_a_two_column_sign(self):
        """Sorting by vertical position alone scrambles columns, because
        side-by-side text never shares an exact top coordinate."""
        sign = [
            line("A2", 0.10, left=0.6),
            line("A1", 0.11, left=0.1),
            line("B2", 0.50, left=0.6),
            line("B1", 0.51, left=0.1),
        ]
        assert texts(sort_reading_order(sign)) == ["A1", "A2", "B1", "B2"]

    def test_rows_further_apart_than_tolerance_stay_separate(self):
        rows = [line("below", 0.60, left=0.0), line("above", 0.10, left=0.9)]
        assert texts(sort_reading_order(rows)) == ["above", "below"]

    def test_rows_are_grouped_by_line_height_not_a_fixed_fraction(self):
        """Regression: a distant sign's lines sit closer together than the old
        fixed 4% tolerance, so a three-line sign collapsed into one row and
        was read left-to-right as gibberish."""
        sign = [
            line("Line two", 0.42, left=0.45, height=0.015),
            line("Line one", 0.40, left=0.40, height=0.015),
            line("Line three", 0.44, left=0.42, height=0.015),
        ]
        assert texts(sort_reading_order(sign)) == ["Line one", "Line two", "Line three"]

    def test_tall_and_short_text_on_one_row_stay_together(self):
        """A headline and a small label beside it share a row when their
        vertical centres line up, whatever their tops do."""
        row = [
            line("left", 0.13, left=0.6, height=0.04),  # centre 0.15
            line("EXIT", 0.10, left=0.1, height=0.10),  # centre 0.15
        ]
        assert texts(sort_reading_order(row)) == ["EXIT", "left"]

    def test_empty_input(self):
        assert sort_reading_order([]) == []


class TestCleaning:
    def test_drops_lines_with_no_letters_or_digits(self):
        """Arrows, rules and stray marks are detections, not text."""
        cleaned = clean_lines([line("->", 0.1), line("EXIT", 0.2), line("|||", 0.3)])
        assert texts(cleaned) == ["EXIT"]

    def test_drops_implausible_text(self):
        """Observed on a carpet texture. Confidence does not rescue it."""
        cleaned = clean_lines([line("J¥Y.A'¢&'.;", 0.1, confidence=0.99), line("EXIT", 0.2)])
        assert texts(cleaned) == ["EXIT"]

    def test_drops_low_confidence_lines(self):
        cleaned = clean_lines([line("noise", 0.1, confidence=0.1), line("EXIT", 0.2)])
        assert texts(cleaned) == ["EXIT"]

    def test_collapses_internal_whitespace(self):
        assert texts(clean_lines([line("  Room   204 ", 0.1)])) == ["Room 204"]

    def test_removes_the_same_line_reported_twice(self):
        """Engines sometimes return one line as two overlapping boxes."""
        cleaned = clean_lines(
            [line("EXIT", 0.10, height=0.05), line("exit,", 0.11, height=0.05)]
        )
        assert texts(cleaned) == ["EXIT"]

    def test_keeps_identical_text_on_different_rows(self):
        """Two doors both marked PUSH are two lines."""
        cleaned = clean_lines(
            [line("PUSH", 0.10, height=0.05), line("PUSH", 0.60, height=0.05)]
        )
        assert texts(cleaned) == ["PUSH", "PUSH"]


class TestConsensus:
    """Several frames of one scene: OCR noise moves between them, text does not."""

    def test_text_seen_in_every_frame_is_kept_with_its_agreement(self):
        readings = [[line("EXIT", 0.10)], [line("EXIT", 0.11)], [line("EXIT", 0.10)]]
        merged = merge_readings(readings)
        assert texts(merged) == ["EXIT"]
        assert merged[0].agreement == 3

    def test_similar_variants_group_and_the_most_confident_wins(self):
        """Degraded frames disagree on characters; exact matching would let
        neither variant reach two votes."""
        readings = [
            [line("204B", 0.1, confidence=0.9)],
            [line("2048", 0.1, confidence=0.6)],
            [line("204B", 0.1, confidence=0.8)],
        ]
        merged = merge_readings(readings)
        assert texts(merged) == ["204B"]
        assert merged[0].agreement == 3

    def test_short_fragment_seen_once_is_dropped(self):
        readings = [[line("EXIT", 0.1), line("Jn", 0.5)], [line("EXIT", 0.1)], [line("EXIT", 0.1)]]
        assert texts(merge_readings(readings)) == ["EXIT"]

    def test_long_wordlike_line_seen_once_survives(self):
        """Requiring agreement outright cost more real text than it saved."""
        readings = [[line("Keep door closed", 0.5)], [], []]
        assert texts(merge_readings(readings)) == ["Keep door closed"]

    def test_merged_position_is_averaged_so_reading_order_still_works(self):
        readings = [
            [line("Second", 0.50, height=0.04), line("First", 0.10, height=0.04)],
            [line("First", 0.12, height=0.04), line("Second", 0.52, height=0.04)],
        ]
        assert texts(sort_reading_order(merge_readings(readings))) == ["First", "Second"]

    def test_single_frame_skips_consensus(self):
        class Fake:
            name = "fake"

            def read(self, frame_jpeg: bytes) -> list[TextLine]:
                return [line("B12", 0.1)]

        # A short code seen once would not survive a multi-frame vote, but a
        # single frame has nothing to vote against.
        assert texts(TextReader(Fake()).read_consensus_sync([b"jpeg"])) == ["B12"]


class TestSpeechFormatting:
    def test_joins_rows_with_pauses(self):
        spoken = format_for_speech([line("EXIT", 0.1), line("Keep door closed", 0.5)])
        assert spoken == "It reads: EXIT. Keep door closed."

    def test_words_on_one_row_are_joined_without_a_pause(self):
        """Engines split a row on wide spacing; the words still belong together."""
        spoken = format_for_speech(
            [line("204B", 0.5, left=0.4, height=0.05), line("Room", 0.5, left=0.1, height=0.05)]
        )
        assert spoken == "It reads: Room 204B."

    def test_applies_reading_order_before_speaking(self):
        spoken = format_for_speech([line("second", 0.8), line("first", 0.1)])
        assert spoken.index("first") < spoken.index("second")

    def test_says_so_when_there_is_no_text(self):
        """Silence would be indistinguishable from a crash."""
        assert format_for_speech([]) == NO_TEXT_FOUND

    def test_whitespace_only_text_is_not_reported_as_readable(self):
        assert format_for_speech([line("   ", 0.1)]) == NO_TEXT_FOUND

    def test_does_not_double_up_punctuation(self):
        assert format_for_speech([line("Room 204B.", 0.1)]) == "It reads: Room 204B."

    def test_strips_stray_marks_a_voice_would_read_aloud(self):
        """A speech engine voices these literally: 'EXIT comma comma'."""
        spoken = format_for_speech([line("EXIT,,", 0.1), line("Conference Room B .", 0.5)])
        assert spoken == "It reads: EXIT. Conference Room B."

    def test_output_is_speakable(self):
        spoken = format_for_speech([line("EXIT", 0.1), line("Room 204B", 0.5)])
        assert not any(ch in spoken for ch in "*_#[]{}<>")


class TestReader:
    def test_reader_without_an_engine_is_unavailable_and_reads_nothing(self):
        reader = TextReader(None)
        assert not reader.available
        assert reader.name == "none"
        assert reader.read_sync(b"anything") == []
        assert reader.read_consensus_sync([b"a", b"b"]) == []

    def test_build_reader_none_disables_ocr(self):
        assert not build_reader("none").available

    def test_engine_failure_degrades_to_no_text(self):
        """This sits in the user's speech path; it must degrade, not crash."""

        class Broken:
            name = "broken"

            def read(self, frame_jpeg: bytes) -> list[TextLine]:
                raise RuntimeError("model exploded")

        assert TextReader(Broken()).read_sync(b"jpeg") == []

    def test_reader_cleans_engine_output(self):
        class Noisy:
            name = "noisy"

            def read(self, frame_jpeg: bytes) -> list[TextLine]:
                return [line("->", 0.1), line("  EXIT ", 0.2), line("x", 0.3, confidence=0.05)]

        assert texts(TextReader(Noisy()).read_sync(b"jpeg")) == ["EXIT"]


# --- Against a real engine --------------------------------------------------

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)  # Pillow >= 10.1
    except TypeError:  # pragma: no cover - old Pillow
        return ImageFont.load_default()


def jpeg(image: Image.Image, quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def sign_jpeg(*lines_of_text: str) -> bytes:
    image = Image.new("RGB", (900, 260 * len(lines_of_text)), "white")
    draw = ImageDraw.Draw(image)
    for index, text in enumerate(lines_of_text):
        draw.text((60, 60 + index * 240), text, fill="black", font=font(110))
    return jpeg(image)


def distant_sign_jpeg(font_px: int) -> bytes:
    """Small text in a large frame: a sign seen from across a room."""
    width, height = 1600, 1200
    image = Image.new("RGB", (width, height), "#d8d8d8")
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [width // 2 - 260, height // 2 - 70, width // 2 + 260, height // 2 + 70],
        fill="white",
        outline="black",
        width=2,
    )
    draw.text(
        (width // 2 - 240, height // 2 - font_px // 2),
        "EXIT 204B",
        fill="black",
        font=font(font_px),
    )
    return jpeg(image)


@pytest.fixture(scope="module")
def reader() -> TextReader:
    engine = build_reader()
    if not engine.available:
        pytest.skip("no OCR engine available on this platform")
    engine.warmup()
    return engine


class TestEngine:
    def test_reads_a_single_word_sign(self, reader):
        assert any("EXIT" in l.text.upper() for l in reader.read_sync(sign_jpeg("EXIT")))

    def test_reads_multiple_lines_in_order(self, reader):
        spoken = format_for_speech(reader.read_sync(sign_jpeg("EXIT", "Room 204B")))
        assert "EXIT" in spoken.upper()
        assert spoken.upper().index("EXIT") < spoken.upper().index("204B")

    def test_reads_small_distant_text(self, reader):
        """Regression: reads were 'sometimes bad'. At 26 px in a 1200 px frame
        (2.2% of height) the text is well under Apple Vision's default floor."""
        assert "204" in format_for_speech(reader.read_sync(distant_sign_jpeg(26)))

    def test_consensus_over_a_burst_keeps_the_sign(self, reader):
        burst = [sign_jpeg("EXIT", "Room 204B")] * 3
        lines = reader.read_consensus_sync(burst)
        spoken = format_for_speech(lines)
        assert "EXIT" in spoken.upper() and "204B" in spoken.upper()
        assert all(l.agreement == 3 for l in lines)

    def test_blank_image_yields_no_text_rather_than_noise(self, reader):
        blank = jpeg(Image.new("RGB", (400, 300), "white"))
        assert format_for_speech(reader.read_sync(blank)) == NO_TEXT_FOUND

    def test_malformed_input_does_not_raise(self, reader):
        assert reader.read_sync(b"not a jpeg") == []

    def test_empty_input_does_not_raise(self, reader):
        assert reader.read_sync(b"") == []
        assert reader.read_consensus_sync([b"", b""]) == []


class TestAppleVisionTuning:
    def test_tuning_beats_the_vision_default_on_small_text(self, reader):
        """Pins the improvement itself, not just the outcome."""
        if reader.name != "apple-vision":
            pytest.skip("Apple Vision specific")
        engine = reader._engine
        image = distant_sign_jpeg(26)

        untuned = engine.recognize(image, minimum_height=0.031)
        tuned = reader.read_sync(image)

        assert "204" not in format_for_speech(untuned), "default unexpectedly read it"
        assert "204" in format_for_speech(tuned)
