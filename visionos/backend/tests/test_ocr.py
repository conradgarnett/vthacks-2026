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

    @staticmethod
    def distant_sign_jpeg(font_px: int) -> bytes:
        """Small text in a large frame: a sign seen from across a room."""
        import io

        from PIL import Image, ImageDraw, ImageFont

        width, height = 1600, 1200
        image = Image.new("RGB", (width, height), "#d8d8d8")
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            [width // 2 - 260, height // 2 - 70, width // 2 + 260, height // 2 + 70],
            fill="white",
            outline="black",
            width=2,
        )
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_px)
        except OSError:  # pragma: no cover - font layout varies by OS build
            font = ImageFont.load_default()
        draw.text(
            (width // 2 - 240, height // 2 - font_px // 2),
            "EXIT 204B",
            fill="black",
            font=font,
        )

        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        return buffer.getvalue()

    def test_reads_small_distant_text(self, ocr):
        """Regression: reads were 'sometimes bad'.

        Vision defaults to a minimum text height of 1/32 of the frame and
        silently drops anything smaller -- which is most real signage. At 26px
        in a 1200px frame (2.2%) the default misses this entirely.
        """
        lines = ocr.read_sync(self.distant_sign_jpeg(26))
        assert "204" in format_for_speech(lines)

    def test_tuning_beats_the_vision_default_on_small_text(self, ocr):
        """Pins the improvement itself, not just the outcome."""
        jpeg = self.distant_sign_jpeg(26)
        untuned = ocr._recognize(jpeg, minimum_height=0.031)
        tuned = ocr.read_sync(jpeg)

        assert "204" not in format_for_speech(untuned), "default unexpectedly read it"
        assert "204" in format_for_speech(tuned)

    def test_tiling_rescues_text_the_full_frame_pass_cannot_see(self, ocr):
        """Vision's minimum text height is a fraction of the frame, so a sign
        across a room is invisible no matter how many pixels the sensor got.
        Measured CER was 1.00 below 2% of frame height before tiling."""
        jpeg = self.distant_sign_jpeg(14)  # ~1.2% of a 1200px frame

        full_only = ocr._read_full(jpeg)
        with_tiles = ocr.read_sync(jpeg)

        assert len(with_tiles) > len(full_only) or not full_only, (
            "tiling added nothing on text the full frame pass missed"
        )

    def test_reading_a_clean_sign_does_not_pay_for_tiling(self, ocr):
        """Large text must not trigger the expensive path: tiles re-read what
        the full frame already got right and attach garbled twins."""
        from backend.ai.ocr import _needs_tiles

        assert not _needs_tiles(ocr._read_full(self.sign_jpeg("EXIT", "Room 204B")))

    def test_blank_image_yields_no_text_rather_than_noise(self, ocr):
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (400, 300), "white").save(buffer, "JPEG")
        assert format_for_speech(ocr.read_sync(buffer.getvalue())) == NO_TEXT_FOUND

    def test_malformed_input_does_not_raise(self, ocr):
        """This sits in the user's speech path; it must degrade, not crash."""
        assert ocr.read_sync(b"not a jpeg") == []


class TestDedupeRegressions:
    """Bugs found by the visionOS-2 collaborator in review.

    Neither shows up in eval/, because every corpus phrase is a single line
    in one place. A reviewer reading the logic caught what the benchmark
    structurally could not.
    """

    @staticmethod
    def line(text: str, top: float, left: float):
        from backend.ai.ocr import TextLine

        return TextLine(text=text, confidence=0.5, top=top, left=left)

    def test_two_words_on_one_row_both_survive(self):
        """An engine that boxes "Room" and "204B" separately must keep both.
        A 0.20 horizontal tolerance treated them as one place and dropped one."""
        from backend.ai.ocr import _dedupe

        kept = _dedupe([self.line("Room", 0.50, 0.30), self.line("204B", 0.50, 0.46)])
        assert {l.text for l in kept} == {"Room", "204B"}

    def test_same_word_in_two_places_both_survive(self):
        """"PUSH" on two different doors is two signs, not one read twice."""
        from backend.ai.ocr import _dedupe

        kept = _dedupe([self.line("PUSH", 0.30, 0.12), self.line("PUSH", 0.72, 0.80)])
        assert len(kept) == 2, "repeated signage collapsed into one"

    def test_garbled_twin_in_the_same_place_is_still_collapsed(self):
        """The behaviour the position rule exists for must not regress."""
        from backend.ai.ocr import _dedupe

        kept = _dedupe(
            [self.line("Departures", 0.40, 0.20), self.line("DLpartiirL", 0.41, 0.21)]
        )
        assert len(kept) == 1
        assert kept[0].text == "Departures", "kept the less plausible variant"
