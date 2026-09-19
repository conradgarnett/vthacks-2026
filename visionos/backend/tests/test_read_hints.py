"""A read that finds nothing on a soft picture says what to do about it.

A webcam on a pair of glasses cannot focus on a label held against it, and
the user cannot see that the picture is blurred. The verdict alone ("I don't
see any readable text") leaves them holding the bottle in the same place.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFilter

from backend.ai.ocr import NO_TEXT_FOUND
from backend.main import BLURRY_HINT, looks_blurry, no_text_response


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
