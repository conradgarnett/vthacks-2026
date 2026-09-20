"""The barcode step's decoder, on a rendered code the way a camera would
see it. OpenCV's detector is built for photographs and rejects a perfectly
crisp synthetic strip, so the render is softened; and OpenCV 5 hands back
one string where 4.x gave a list, which once turned every code into a run
of single characters."""

from __future__ import annotations

import io

import pytest

from backend.alerts.barcode import decode_barcodes, valid_ean

# EAN-13 from the standard tables: left half in L or G parity by the first
# digit, right half in R, guards 101 / 01010 / 101.
_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def ean13_modules(code: str) -> str:
    bits = "101"
    for digit, kind in zip(code[1:7], _PARITY[int(code[0])]):
        bits += (_L if kind == "L" else _G)[int(digit)]
    bits += "01010"
    for digit in code[7:]:
        bits += _R[int(digit)]
    return bits + "101"


def render(code: str, blur: float = 1.0, module_px: int = 4) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    bits = ean13_modules(code)
    width = len(bits) * module_px
    image = Image.new("RGB", (width + 240, 400), (240, 236, 228))
    draw = ImageDraw.Draw(image)
    draw.rectangle([80, 40, width + 160, 360], fill=(255, 255, 255))
    for index, bit in enumerate(bits):
        if bit == "1":
            x = 120 + index * module_px
            draw.rectangle([x, 80, x + module_px - 1, 300], fill=(0, 0, 0))
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def _detector_present() -> bool:
    try:
        import cv2

        return hasattr(getattr(cv2, "barcode", None), "BarcodeDetector")
    except Exception:
        return False


@pytest.mark.skipif(not _detector_present(), reason="this OpenCV has no barcode module")
def test_a_rendered_code_decodes_as_the_product_code():
    codes = decode_barcodes(render("0037600106009"))
    # UPC-A is EAN-13 with a leading zero; the decoder may return either.
    assert codes and codes[0].lstrip("0") == "37600106009", codes
    assert all(valid_ean(code) for code in codes)


@pytest.mark.skipif(not _detector_present(), reason="this OpenCV has no barcode module")
def test_the_wikipedia_example_decodes():
    assert decode_barcodes(render("5901234123457")) == ["5901234123457"]


def test_no_frame_is_no_code():
    assert decode_barcodes(b"") == []
    assert decode_barcodes(b"not a jpeg") == []
