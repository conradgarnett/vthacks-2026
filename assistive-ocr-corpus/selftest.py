#!/usr/bin/env python3
"""Prove the install works: `python selftest.py`, about 30 seconds.

Renders one sample from every corpus and exercises the scorer. It does not
need an OCR engine -- this package measures readers, it does not contain one.

The generators fail quietly when they fail at all: a missing font directory
makes `bundled_fonts()` return an empty list and every font corpus comes out
empty, with no exception. So this checks the things that would otherwise be
silently wrong -- fonts found, images non-blank, ground truth present, no-text
corpora carrying no ground truth.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "generators"))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(label)


def _looks_rendered(jpeg: bytes) -> bool:
    """Decodes, has size, and is not one flat colour."""
    from PIL import Image

    image = Image.open(io.BytesIO(jpeg))
    if min(image.size) < 100:
        return False
    extrema = image.convert("L").getextrema()
    return extrema[1] - extrema[0] > 20


def main() -> int:
    print("dependencies")
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401

        check("pillow and numpy import", True)
    except ImportError as exc:
        check("pillow and numpy import", False, str(exc))
        print("\n  pip install -r requirements.txt")
        return 1

    print("\ntypefaces")
    from typefaces import bundled_fonts, platform_fonts

    bundled = bundled_fonts()
    check(f"bundled faces found ({len(bundled)})", len(bundled) >= 30,
          "fonts/ missing or empty -- font corpora would render empty")
    check(f"platform faces found ({len(platform_fonts())})", bool(platform_fonts()))

    print("\ngenerators")
    import allergens
    import corpus
    import fonts as font_corpus
    import medicine
    import product_labels

    signage = corpus.build_corpus(n=1, frames=1, fonts=bundled)
    check("signage renders", _looks_rendered(signage[0].frames[0]))
    check("signage carries its phrase", bool(signage[0].truth))

    blanks = corpus.build_textureless_corpus(n=1)
    check("textureless renders", _looks_rendered(blanks[0][0]))

    vial = medicine.build_medicine_corpus(n=1, frames=1)[0]
    check("medicine renders", _looks_rendered(vial["frames"][0]))
    check("medicine carries drug and directions",
          bool(vial["drug"]) and bool(vial["directions"]))

    # Compared on the rendered bytes, not the fields: the two splits draw from
    # one vocabulary, so the same drug name turning up in both is expected and
    # says nothing. What the disjoint seed range holds out is label instances
    # -- layout, scale, curvature, tint, capture -- which is what threshold
    # overfitting attaches to.
    held = medicine.build_holdout_corpus(n=1, frames=1)[0]
    check("held-out split is a different draw",
          held["frames"][0] != vial["frames"][0],
          "identical to tuning -- the disjoint seed range is not taking effect")

    pack = product_labels.build_packaging_corpus(n=1, frames=1)[0]
    check("packaging renders", _looks_rendered(pack["frames"][0]))

    symbols = product_labels.build_symbol_corpus(n=1, frames=1)[0]
    symbol_frames = symbols["frames"] if isinstance(symbols, dict) else symbols
    check("symbols render", _looks_rendered(symbol_frames[0]))

    allergen = allergens.build_allergen_corpus(n=1, frames=1)[0]
    check("allergens render", _looks_rendered(allergen["frames"][0]))
    check("allergens carry a statement and a truth set",
          bool(allergen["statement"]) and isinstance(allergen["truth"], set))

    faces = font_corpus.build_font_corpus(frames=1, phrases_per_font=1, bundled=True)
    check(f"font benchmark builds ({len(faces)} samples)", len(faces) >= 30)

    print("\ndeterminism")
    a = corpus.build_corpus(n=1, seed=5, frames=1, fonts=bundled)[0]
    b = corpus.build_corpus(n=1, seed=5, frames=1, fonts=bundled)[0]
    check("same seed gives identical bytes", a.frames[0] == b.frames[0],
          "scores will not reproduce")

    print("\ncamera tiers")
    import webcam
    from PIL import Image

    base = Image.open(io.BytesIO(signage[0].frames[0]))
    sizes = {}
    for name, camera in sorted(webcam.TIERS.items()):
        jpeg = webcam.through_camera(base, camera)
        sizes[name] = len(jpeg)
        check(f"{name} tier captures", _looks_rendered(jpeg))
    check("cheap tier costs bytes against phone", sizes["cheap"] < sizes["phone"],
          f"{sizes} -- expected the degraded tier to compress smaller")

    print("\nscorer")
    from metrics import cer, edit_distance, score

    check("edit distance", edit_distance("kitten", "sitting") == 3)
    check("CER is 0 on an exact read", cer("Room 204B", "Room 204B") == 0.0)
    check("CER catches a digit swap", 0 < cer("Room 2048", "Room 204B") < 0.2,
          "this is the failure a contains-the-word check misses")
    check("CER is capped at 1", cer("x" * 500, "EXIT") == 1.0,
          "one catastrophic sample would dominate the mean")

    result = score(
        ["EXIT", "Room 2048", ""],
        ["EXIT", "Room 204B", "Gate A12"],
        no_text_predictions=["", "It reads: Ip. h.jlil."],
    )
    check("counts exact, silent and invented separately",
          (result.exact, result.silent, result.hallucinated) == (1, 1, 1))
    check("hallucination is scored over no-text samples only",
          result.no_text_samples == 2 and abs(result.hallucination_rate - 0.5) < 1e-9)

    try:
        score(["a"], ["a", "b"])
        check("mismatched lengths raise", False, "silently scored a misalignment")
    except ValueError:
        check("mismatched lengths raise", True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
