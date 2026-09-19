"""A read that finds nothing on a soft picture says what to do about it.

A webcam on a pair of glasses cannot focus on a label held against it, and
the user cannot see that the picture is blurred. The verdict alone ("I don't
see any readable text") leaves them holding the bottle in the same place.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFilter

from backend.ai.ocr import NO_TEXT_FOUND
from backend.main import BLURRY_HINT, looks_blurry, no_text_response, text_holder, with_holder


def label(blur: float) -> bytes:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    for row in range(8):
        draw.text((80, 60 + row * 90), "TAKE 1 TABLET BY MOUTH DAILY", fill="black")
        draw.rectangle([80, 100 + row * 90, 900, 104 + row * 90], fill="black")
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def test_a_sharp_frame_is_not_called_blurry():
    assert not looks_blurry([label(0)])


def test_a_heavily_blurred_burst_is_blurry():
    assert looks_blurry([label(6.0), label(6.0), label(6.0)])


def test_one_sharp_frame_in_the_burst_is_enough():
    """Consensus reads from the sharpest frame; the advice is for when there
    was none."""
    assert not looks_blurry([label(6.0), label(0), label(6.0)])


def test_the_hint_is_only_added_to_a_blurry_no_text_read():
    assert no_text_response([label(6.0)]) == f"{NO_TEXT_FOUND} {BLURRY_HINT}"
    assert no_text_response([label(0)]) == NO_TEXT_FOUND


def test_the_hint_says_where_to_hold_it():
    assert "hand's length" in BLURRY_HINT and "hold still" in BLURRY_HINT


def test_no_frames_is_not_blurry():
    assert not looks_blurry([])


class Seen:
    """The fields of a tracked object that naming the text's holder uses."""

    def __init__(self, label, azimuth=0.0, distance=1.0, confidence=0.9, visible=True):
        self.label = label
        self.azimuth_deg = azimuth
        self.distance_m = distance
        self.confidence = confidence
        self.visible = visible


class TestTextHolder:
    def test_the_nearest_central_object_is_named(self):
        objects = [Seen("door", azimuth=10.0, distance=3.0), Seen("bottle", azimuth=-5.0, distance=0.4)]
        assert text_holder(objects) == "bottle"

    def test_an_unsure_object_is_not_named(self):
        """The same 80% floor as every other guess: a wrong holder is
        misinformation the user cannot check."""
        assert text_holder([Seen("bottle", confidence=0.6)]) is None

    def test_an_object_at_the_edge_of_view_is_not_the_holder(self):
        assert text_holder([Seen("sign", azimuth=60.0)]) is None

    def test_an_object_that_left_view_is_not_the_holder(self):
        assert text_holder([Seen("bottle", visible=False)]) is None

    def test_a_person_is_never_what_text_is_on(self):
        assert text_holder([Seen("person", distance=0.5), Seen("box", distance=1.0)]) == "box"

    def test_nothing_tracked_names_nothing(self):
        assert text_holder([]) is None

    def test_the_holder_prefixes_the_reading(self):
        assert with_holder("It reads: Diet Cola.", "bottle") == "On the bottle, it reads: Diet Cola."

    def test_no_holder_leaves_the_reading_alone(self):
        assert with_holder("It reads: EXIT.", None) == "It reads: EXIT."

    def test_the_refusal_is_not_prefixed(self):
        assert with_holder("I don't see any readable text.", "bottle") == "I don't see any readable text."
