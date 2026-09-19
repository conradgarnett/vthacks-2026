"""Text reading.

Reading order carries most of the risk. Vision returns observations in no
guaranteed order, and a sign spoken bottom-up or zig-zagged across columns is
worse than one not read at all -- the user has no way to tell it was scrambled.
"""

from __future__ import annotations

import pytest

from backend.ai.ocr import (
    NO_TEXT_FOUND,
    AppleVisionOCR,
    TextLine,
    format_for_speech,
    sort_reading_order,
)


def line(text: str, top: float, left: float = 0.0, confidence: float = 0.9) -> TextLine:
    return TextLine(text=text, confidence=confidence, top=top, left=left)


class TestReadingOrder:
    def test_sorts_top_to_bottom(self):
        scrambled = [line("third", 0.8), line("first", 0.1), line("second", 0.45)]
        assert [l.text for l in sort_reading_order(scrambled)] == [
            "first",
            "second",
            "third",
        ]

    def test_reads_left_to_right_within_a_row(self):
        """Two labels side by side are one row, not two."""
        row = [line("right", 0.30, left=0.7), line("left", 0.31, left=0.1)]
        assert [l.text for l in sort_reading_order(row)] == ["left", "right"]

    def test_does_not_zigzag_across_a_two_column_sign(self):
        """Sorting by vertical position alone scrambles columns, because
        side-by-side text never shares an exact top coordinate."""
        sign = [
            line("A2", 0.10, left=0.6),
            line("A1", 0.11, left=0.1),
            line("B2", 0.50, left=0.6),
            line("B1", 0.51, left=0.1),
        ]
        assert [l.text for l in sort_reading_order(sign)] == ["A1", "A2", "B1", "B2"]

    def test_rows_further_apart_than_tolerance_stay_separate(self):
        rows = [line("below", 0.60, left=0.0), line("above", 0.10, left=0.9)]
        assert [l.text for l in sort_reading_order(rows)] == ["above", "below"]

    def test_empty_input(self):
        assert sort_reading_order([]) == []


class TestSpeechFormatting:
    def test_joins_lines_with_pauses(self):
        spoken = format_for_speech([line("EXIT", 0.1), line("Keep door closed", 0.5)])
        assert spoken == "It reads: EXIT. Keep door closed."

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

    def test_output_is_speakable(self):
        spoken = format_for_speech([line("EXIT", 0.1), line("Room 204B", 0.5)])
        assert not any(ch in spoken for ch in "*_#[]{}<>")


class TestAppleVision:
    """Integration against the real framework; skipped where unavailable."""

    @pytest.fixture(scope="class")
    def ocr(self):
        engine = AppleVisionOCR()
        if not engine.available:
            pytest.skip("Apple Vision unavailable on this platform")
        engine.warmup()
        return engine

    @staticmethod
    def sign_jpeg(*texts: str) -> bytes:
        import io

        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (900, 260 * len(texts)), "white")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 110)
        except OSError:  # pragma: no cover - font layout varies by OS build
            font = ImageFont.load_default()
        for index, text in enumerate(texts):
            draw.text((60, 60 + index * 240), text, fill="black", font=font)

        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        return buffer.getvalue()

    def test_reads_a_single_word_sign(self, ocr):
        lines = ocr.read_sync(self.sign_jpeg("EXIT"))
        assert any("EXIT" in l.text.upper() for l in lines)

    def test_reads_multiple_lines_in_order(self, ocr):
        lines = ocr.read_sync(self.sign_jpeg("EXIT", "Room 204B"))
        spoken = format_for_speech(lines)
        assert "EXIT" in spoken.upper()
        assert spoken.upper().index("EXIT") < spoken.upper().index("204B")

    def test_blank_image_yields_no_text_rather_than_noise(self, ocr):
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (400, 300), "white").save(buffer, "JPEG")
        assert format_for_speech(ocr.read_sync(buffer.getvalue())) == NO_TEXT_FOUND

    def test_malformed_input_does_not_raise(self, ocr):
        """This sits in the user's speech path; it must degrade, not crash."""
        assert ocr.read_sync(b"not a jpeg") == []
