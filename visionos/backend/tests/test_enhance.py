"""Image transforms for text the recognizer cannot read as-is.

Cursive is why this exists: faces like Great Vibes and Pacifico return no
observations at all, so there is nothing for post-processing to repair and the
only lever left is the image itself.

The tests care most about the guard. Every variant is a guess, and a guess
that reads confidently is the dangerous kind -- accepting any improvement
tripled hallucination on symbol-only images.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.ai.enhance import variants
from backend.ai.ocr import TextLine, _contains_a_word, _reading_strength


def frame(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (600, 300), color).save(buffer, "JPEG")
    return buffer.getvalue()


class TestVariants:
    def test_produces_several_named_transforms(self):
        out = variants(frame())
        assert len(out) >= 6
        names = {name for name, _ in out}
        # Each targets a different property of hard text.
        assert any("deslant" in n for n in names), "no slant correction"
        assert "thicken" in names, "no stroke thickening"
        assert "binarize" in names, "no contrast split"
        assert any("unwarp" in n for n in names), "no curvature correction"

    def test_every_variant_is_a_decodable_image(self):
        for name, payload in variants(frame()):
            image = Image.open(io.BytesIO(payload))
            image.load()
            assert min(image.size) > 0, f"{name} produced an empty image"

    def test_malformed_input_degrades_quietly(self):
        """This sits in the read path; it must not raise."""
        assert variants(b"not a jpeg") == []


class TestAcceptanceGuard:
    @staticmethod
    def lines(*texts: str):
        return [
            TextLine(text=t, confidence=0.5, top=0.4, left=0.1) for t in texts
        ]

    def test_a_real_word_is_recognized(self):
        assert _contains_a_word(self.lines("Reception"))
        assert _contains_a_word(self.lines("DIET COLA"))

    @pytest.mark.parametrize("junk", ["Ip h.jlil", "4r ai", "Il Illl", "xq zt"])
    def test_enhanced_symbol_junk_is_not_a_word(self, junk):
        """The discriminator between a rescued script face and a barcode:
        transforming a barcode yields shapes that score, but no word."""
        assert not _contains_a_word(self.lines(junk))

    def test_a_near_miss_counts_once_corrected(self):
        """"eception" is one edit from a real word, so it should pass."""
        assert _contains_a_word(self.lines("eception"))

    def test_strength_prefers_longer_plausible_text(self):
        assert _reading_strength(self.lines("Reception")) > _reading_strength(
            self.lines("Rec")
        )

    def test_empty_reading_has_no_strength(self):
        assert _reading_strength([]) == 0
