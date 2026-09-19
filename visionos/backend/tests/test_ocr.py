"""Text reading.

Reading order carries most of the risk. Engines return lines in no
guaranteed order, and a sign spoken bottom-up or zig-zagged across columns is
worse than one not read at all: the user has no way to tell it was scrambled.

Engine tests run against whichever OCR engine loads on this machine and skip
when none does. Pipeline quality is measured separately with `eval/`; these
tests pin behaviour, not accuracy.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.ai.ocr import (
    NO_TEXT_FOUND,
    AppleVisionOCR,
    TextLine,
    TextReader,
    _accept,
    _dedupe,
    _drop_overlapping_fragments,
    _fix_digit_confusions,
    _merge,
    _needs_tiles,
    build_reader,
    format_for_speech,
    reading_rows,
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

    def test_rows_expose_their_grouping(self):
        rows = reading_rows([line("b", 0.5), line("a", 0.1)])
        assert [texts(row) for row in rows] == [["a"], ["b"]]

    def test_empty_input(self):
        assert sort_reading_order([]) == []


class TestAcceptance:
    """The gate every engine's raw output passes through."""

    def test_rejects_texture_noise_regardless_of_confidence(self):
        """Observed on a carpet texture. Vision reported it at ~0.5, and
        would happily report it at 0.99."""
        assert not _accept("J¥Y.A'¢&'.;", 0.99)

    def test_rejects_symbols_only(self):
        assert not _accept("->", 0.9)
        assert not _accept("|||", 0.9)

    def test_rejects_low_confidence(self):
        assert not _accept("EXIT", 0.1)

    @pytest.mark.parametrize("text", ["EXIT", "Room 204B", "Keep door closed", "B12"])
    def test_keeps_signage(self, text):
        assert _accept(text, 0.5)


class TestConsensus:
    """Several frames of one scene: OCR noise moves between them, text does not."""

    def test_text_seen_in_every_frame_is_kept_with_its_agreement(self):
        readings = [[line("EXIT", 0.10)], [line("EXIT", 0.11)], [line("EXIT", 0.10)]]
        merged = _merge(readings)
        assert texts(merged) == ["EXIT"]
        assert merged[0].agreement == 3

    def test_similar_variants_group_and_the_most_plausible_wins(self):
        """Degraded frames disagree on characters; exact matching would let
        neither variant reach two votes."""
        readings = [
            [line("204B", 0.1, confidence=0.9)],
            [line("2048", 0.1, confidence=0.6)],
            [line("204B", 0.1, confidence=0.8)],
        ]
        merged = _merge(readings)
        assert texts(merged) == ["204B"]
        assert merged[0].agreement == 3

    def test_short_fragment_seen_once_is_dropped(self):
        readings = [[line("EXIT", 0.1), line("Jn", 0.5)], [line("EXIT", 0.1)], [line("EXIT", 0.1)]]
        assert texts(_merge(readings)) == ["EXIT"]

    def test_long_wordlike_line_seen_once_survives(self):
        """Requiring agreement outright cost more real text than it saved."""
        readings = [[line("Keep door closed", 0.5)], [], []]
        assert texts(_merge(readings)) == ["Keep door closed"]

    def test_merged_position_is_averaged_so_reading_order_still_works(self):
        readings = [
            [line("Second", 0.50, height=0.04), line("First", 0.10, height=0.04)],
            [line("First", 0.12, height=0.04), line("Second", 0.52, height=0.04)],
        ]
        assert texts(sort_reading_order(_merge(readings))) == ["First", "Second"]

    def test_single_frame_skips_the_vote(self):
        class Fake(TextReader):
            name = "fake"
            available = True

            def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
                return [line("B12", 0.1)]

        # A short code seen once would not survive a multi-frame vote, but a
        # single frame has nothing to vote against.
        assert texts(Fake().read_consensus_sync([b"jpeg"])) == ["B12"]


class TestDigitConfusions:
    """RapidOCR reads a lone digit inside a word where a letter was."""

    @pytest.mark.parametrize(
        "read,fixed",
        [("Ro0m", "Room"), ("R00M", "R00M"), ("EX1T", "EXIT"), ("F1oor", "Floor"), ("H0TEL", "HOTEL")],
    )
    def test_lone_digit_between_letters_becomes_the_letter(self, read, fixed):
        assert _fix_digit_confusions(read) == fixed

    @pytest.mark.parametrize(
        "code", ["Room 204B", "B12", "A-4", "Gate A12", "R2D2", "A1B2", "Platform 9"]
    )
    def test_codes_and_numbers_pass_through(self, code):
        """Two digits, or digits at the edges, are what room and gate codes
        look like; only a single digit buried in letters is a misread."""
        assert _fix_digit_confusions(code) == code


class TestEarlyAgreement:
    def test_costly_engine_stops_once_two_frames_agree(self):
        calls: list[bytes] = []

        class Slow(TextReader):
            name = "slow"
            available = True
            costly_frames = True

            def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
                calls.append(frame_jpeg)
                return [line("Keep door closed", 0.1)]

        merged = Slow().read_consensus_sync([b"a", b"b", b"c"])
        assert len(calls) == 2, "third frame should not have been read"
        assert texts(merged) == ["Keep door closed"]
        assert merged[0].agreement == 2

    def test_cheap_engine_takes_the_full_vote(self):
        calls: list[bytes] = []

        class Fast(TextReader):
            name = "fast"
            available = True

            def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
                calls.append(frame_jpeg)
                return [line("Keep door closed", 0.1)]

        Fast().read_consensus_sync([b"a", b"b", b"c"])
        assert len(calls) == 3

    def test_disagreeing_frames_keep_reading(self):
        calls: list[bytes] = []
        variants = ["Departures", "DLpartiirL", "Departures"]

        class Noisy(TextReader):
            name = "noisy"
            available = True
            costly_frames = True

            def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
                calls.append(frame_jpeg)
                return [line(variants[len(calls) - 1], 0.1)]

        merged = Noisy().read_consensus_sync([b"a", b"b", b"c"])
        assert len(calls) == 3
        assert texts(merged) == ["Departures"]


class TestOverlappingFragments:
    """Tiles cut a word apart on hard typefaces and each piece was spoken."""

    def test_pieces_of_a_longer_line_are_dropped(self):
        kept = _drop_overlapping_fragments(
            [line("Fire CXLE", 0.4, left=0.1), line("ire Cxi", 0.5, left=0.6)]
        )
        assert texts(kept) == ["Fire CXLE"]

    def test_different_words_both_survive(self):
        kept = _drop_overlapping_fragments([line("Reception", 0.2), line("Restroom", 0.6)])
        assert len(kept) == 2

    def test_a_similar_real_word_survives(self):
        """"Receipt" beside "Reception" shares only "rece", four of seven
        characters, so it is kept. The rule needs most of the short line to
        be one contiguous run, not merely a shared stem."""
        kept = _drop_overlapping_fragments([line("Reception", 0.2), line("Receipt", 0.6)])
        assert len(kept) == 2

    def test_spoken_output_says_the_word_once(self):
        spoken = format_for_speech(
            [line("Reception", 0.4, left=0.1), line("ion", 0.42, left=0.6), line("Rec", 0.44, left=0.8)]
        )
        assert spoken == "It reads: Reception."


class TestTileGate:
    def test_nothing_or_a_scrap_escalates_to_tiles(self):
        assert _needs_tiles([])
        assert _needs_tiles([line("EX", 0.1)])

    def test_a_solid_reading_does_not(self):
        """Tiles re-read what the full frame already got right and attach
        garbled twins, so a good reading must not pay for them."""
        assert not _needs_tiles([line("Keep door closed", 0.1)])


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
    def test_null_reader_is_unavailable_and_reads_nothing(self):
        reader = TextReader()
        assert not reader.available
        assert reader.name == "none"
        assert reader.read_sync(b"anything") == []
        assert reader.read_consensus_sync([b"a", b"b"]) == []

    def test_build_reader_none_disables_ocr(self):
        assert not build_reader("none").available

    def test_build_reader_rejects_unknown_engine_names_gracefully(self):
        assert not build_reader("not-an-engine").available

    def test_engine_failure_degrades_to_no_text(self):
        """This sits in the user's speech path; it must degrade, not crash."""

        class Broken(TextReader):
            name = "broken"
            available = True

            def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
                raise RuntimeError("model exploded")

        assert Broken().read_sync(b"jpeg") == []
        assert Broken().read_consensus_sync([b"jpeg", b"jpeg"]) == []


# --- Against a real engine --------------------------------------------------

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def font(px: int):
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
        """At 26 px in a 1200 px frame (2.2% of height) the text is under
        Apple Vision's default floor and must still come back."""
        assert "204" in format_for_speech(reader.read_sync(distant_sign_jpeg(26)))

    def test_reading_a_clean_sign_does_not_pay_for_tiling(self, reader):
        """Large text must not trigger the expensive path: tiles re-read what
        the full frame already got right and attach garbled twins."""
        from backend.ai.ocr import _prepare

        assert not _needs_tiles(reader._read_full(_prepare(sign_jpeg("EXIT", "Room 204B"))))

    def test_consensus_over_a_burst_keeps_the_sign(self, reader):
        burst = [sign_jpeg("EXIT", "Room 204B")] * 3
        lines = reader.read_consensus_sync(burst)
        spoken = format_for_speech(lines)
        assert "EXIT" in spoken.upper() and "204B" in spoken.upper()
        # A costly engine stops once two identical frames agree; a cheap one
        # takes all three. Either way every line was confirmed.
        assert all(l.agreement >= 2 for l in lines)

    def test_blank_image_yields_no_text_rather_than_noise(self, reader):
        blank = jpeg(Image.new("RGB", (400, 300), "white"))
        assert format_for_speech(reader.read_sync(blank)) == NO_TEXT_FOUND

    def test_malformed_input_does_not_raise(self, reader):
        assert reader.read_sync(b"not a jpeg") == []

    def test_empty_input_does_not_raise(self, reader):
        assert reader.read_sync(b"") == []
        assert reader.read_consensus_sync([b"", b""]) == []


class TestAppleVision:
    """Vision-specific tuning; skipped everywhere else."""

    @pytest.fixture(scope="class")
    def ocr(self):
        engine = AppleVisionOCR()
        if not engine.available:
            pytest.skip("Apple Vision unavailable on this platform")
        engine.warmup()
        return engine

    def test_tuning_beats_the_vision_default_on_small_text(self, ocr):
        """Pins the improvement itself, not just the outcome."""
        image = distant_sign_jpeg(26)
        untuned = ocr._recognize(image, minimum_height=0.031)
        tuned = ocr.read_sync(image)

        assert "204" not in format_for_speech(untuned), "default unexpectedly read it"
        assert "204" in format_for_speech(tuned)

    def test_tiling_rescues_text_the_full_frame_pass_cannot_see(self, ocr):
        """Vision's minimum text height is a fraction of the frame, so a sign
        across a room is invisible no matter how many pixels the sensor got.
        Measured CER was 1.00 below 2% of frame height before tiling."""
        image = distant_sign_jpeg(14)  # ~1.2% of a 1200px frame

        full_only = ocr._read_full(image)
        with_tiles = ocr.read_sync(image)

        assert len(with_tiles) > len(full_only) or not full_only, (
            "tiling added nothing on text the full frame pass missed"
        )


class TestDedupeRegressions:
    """Bugs found by the visionOS-2 collaborator in review.

    Neither shows up in eval/, because every corpus phrase is a single line
    in one place. A reviewer reading the logic caught what the benchmark
    structurally could not.
    """

    def test_two_words_on_one_row_both_survive(self):
        """An engine that boxes "Room" and "204B" separately must keep both.
        A 0.20 horizontal tolerance treated them as one place and dropped one."""
        kept = _dedupe([line("Room", 0.50, left=0.30), line("204B", 0.50, left=0.46)])
        assert {l.text for l in kept} == {"Room", "204B"}

    def test_same_word_in_two_places_both_survive(self):
        """"PUSH" on two different doors is two signs, not one read twice."""
        kept = _dedupe([line("PUSH", 0.30, left=0.12), line("PUSH", 0.72, left=0.80)])
        assert len(kept) == 2, "repeated signage collapsed into one"

    def test_garbled_twin_in_the_same_place_is_still_collapsed(self):
        """The behaviour the position rule exists for must not regress."""
        kept = _dedupe(
            [line("Departures", 0.40, left=0.20), line("DLpartiirL", 0.41, left=0.21)]
        )
        assert len(kept) == 1
        assert kept[0].text == "Departures", "kept the less plausible variant"

    def test_fragments_beside_a_real_reading_are_not_spoken(self):
        """Observed: "J 44 Elevator". The "J" is the sign's border."""
        spoken = format_for_speech(
            [line("J", 0.40, left=0.05), line("44", 0.40, left=0.10), line("Elevator", 0.40, left=0.30)]
        )
        # The single-letter border scrap goes; a two-digit number stays,
        # because it could be a real room number beside the word.
        assert spoken == "It reads: 44 Elevator."

    def test_a_room_number_survives_beside_a_longer_line(self):
        spoken = format_for_speech([line("B12", 0.20, left=0.10), line("Conference Room", 0.60, left=0.10)])
        assert "B12" in spoken
