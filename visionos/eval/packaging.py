"""Packaging corpus: curved surfaces, display fonts, and non-text symbols.

The signage corpus in corpus.py is the easy case -- flat, high contrast, one
line of a standard typeface. Consumer packaging is where reading actually gets
hard, and it is what a blind user most often holds:

  curvature      a can or bottle bends every glyph
  display fonts  script, condensed, engraved, handwritten
  small print    ingredients set at a fraction of the label
  symbols        (R) (TM) barcodes, recycling marks, nutrition icons

Symbols are the dangerous part, and they get their own corpus with no text at
all. A recycling triangle or a barcode read as words is worse than silence: a
blind user cannot glance at the can to discover the assistant invented an
ingredient. Anything spoken on those images is a failure by construction.
"""

from __future__ import annotations

import io
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from typefaces import bundled_fonts

W, H = 1600, 1200

# Display and script faces, plus condensed and engraved. These are what brands
# actually use, and they are markedly harder than Helvetica.
DISPLAY_FONTS = [
    "/System/Library/Fonts/Supplemental/Brush Script.ttf",
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    "/System/Library/Fonts/Supplemental/Chalkduster.ttf",
    "/System/Library/Fonts/Supplemental/Marker Felt.ttc",
    "/System/Library/Fonts/Supplemental/Papyrus.ttc",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Copperplate.ttc",
    "/System/Library/Fonts/Supplemental/Didot.ttc",
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
    "/System/Library/Fonts/Supplemental/Baskerville.ttc",
    "/System/Library/Fonts/Supplemental/American Typewriter.ttc",
    "/System/Library/Fonts/Supplemental/Optima.ttc",
    "/System/Library/Fonts/Supplemental/Trattatello.ttf",
]

# Wording from real packaging, including the marks that ride along with it.
PRODUCT_TEXT = [
    ("Diet Cola", "Zero Sugar"),
    ("Sparkling Water", "Natural Lime"),
    ("Orange Juice", "No Pulp"),
    ("Whole Milk", "Vitamin D"),
    ("Tomato Soup", "Low Sodium"),
    ("Greek Yogurt", "Strawberry"),
    ("Energy Drink", "Sugar Free"),
    ("Almond Butter", "Unsalted"),
    ("Iced Tea", "Lemon"),
    ("Ginger Ale", "Caffeine Free"),
]

BRAND_MARKS = ["®", "™", "©"]

# Off macOS none of the faces above exist; the committed display faces in
# eval/fonts/ stand in, so the corpus renders everywhere. On a Mac the list
# is unchanged, so its history stays comparable.
DISPLAY_FONTS = [f for f in DISPLAY_FONTS if os.path.exists(f)] or bundled_fonts()


def _font(path: str, px: int):
    for candidate in (path, *bundled_fonts()):
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def _curve(img: Image.Image, strength: float) -> Image.Image:
    """Bend the image horizontally, as a cylindrical label does.

    Columns near the edges compress and shift vertically, which is what makes
    a can harder to read than a flat box.
    """
    source = np.asarray(img)
    out = np.zeros_like(source)
    height, width = source.shape[:2]

    for x in range(width):
        # -1 at the left edge, +1 at the right
        u = (x / (width - 1)) * 2 - 1
        shift = int(strength * height * 0.10 * (u * u))
        # Horizontal compression toward the edges
        src_x = int((math.asin(max(-1.0, min(1.0, u * 0.92))) / (math.pi / 2))
                    * 0.5 * (width - 1) + 0.5 * (width - 1))
        src_x = max(0, min(width - 1, src_x))
        column = source[:, src_x]
        if shift > 0:
            out[shift:, x] = column[:height - shift]
            out[:shift, x] = column[0]
        else:
            out[:, x] = column
    return Image.fromarray(out)


def draw_barcode(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, rng) -> None:
    """The single most reliable way to make an OCR engine invent text."""
    cursor = x
    while cursor < x + w:
        bar = rng.choice([3, 3, 5, 8, 11])
        if rng.random() < 0.55:
            d.rectangle([cursor, y, cursor + bar, y + h], fill="#0a0a0a")
        cursor += bar + rng.choice([3, 4, 6])


def draw_recycling(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    """Three chasing arrows: a triangle of thick strokes with arrowheads."""
    for i in range(3):
        a0 = math.radians(90 + i * 120)
        a1 = math.radians(90 + i * 120 + 84)
        p0 = (cx + r * math.cos(a0), cy - r * math.sin(a0))
        p1 = (cx + r * math.cos(a1), cy - r * math.sin(a1))
        d.line([p0, p1], fill="#16610e", width=max(3, r // 5))
        head = math.radians(90 + i * 120 + 84)
        hx, hy = cx + r * math.cos(head), cy - r * math.sin(head)
        d.polygon(
            [
                (hx, hy),
                (hx - r * 0.30, hy - r * 0.16),
                (hx - r * 0.12, hy + r * 0.28),
            ],
            fill="#16610e",
        )


def draw_nutrition_icons(d: ImageDraw.ImageDraw, x: int, y: int, rng) -> None:
    """Circular traffic-light badges, as used on European packaging."""
    for i in range(4):
        cx = x + i * 150
        d.ellipse([cx, y, cx + 118, y + 118],
                  outline=rng.choice(["#166534", "#b45309", "#b91c1c"]), width=7)
        d.ellipse([cx + 26, y + 26, cx + 92, y + 92],
                  fill=rng.choice(["#dcfce7", "#fef3c7", "#fee2e2"]))


def draw_flourish(d: ImageDraw.ImageDraw, x: int, y: int, w: int, rng) -> None:
    """Decorative scrollwork: curved strokes that resemble cursive letters."""
    for _ in range(5):
        y0 = y + rng.randint(-24, 24)
        d.arc([x, y0, x + w, y0 + rng.randint(48, 110)],
              start=rng.randint(150, 210), end=rng.randint(330, 390),
              fill="#3f3f46", width=rng.choice([3, 4, 6]))


def render_package(text_lines, font_path: str, scale: float, rng) -> Image.Image:
    """A label: product text, a brand mark, small print and symbols."""
    base = rng.choice(["#b91c1c", "#1d4ed8", "#065f46", "#f4f4f5", "#18181b", "#ea580c"])
    ink = "#f8fafc" if base not in ("#f4f4f5",) else "#18181b"

    img = Image.new("RGB", (W, H), "#6b7280")
    d = ImageDraw.Draw(img)

    # The label panel, inset from a background so edges are in frame.
    panel = [W // 2 - 520, H // 2 - 400, W // 2 + 520, H // 2 + 400]
    d.rectangle(panel, fill=base)

    # Brand banner behind the product name -- text over a filled shape.
    d.rectangle([panel[0], H // 2 - 250, panel[2], H // 2 - 60], fill="#00000022")

    title_px = max(26, int(H * 0.085 * scale))
    sub_px = max(18, int(H * 0.042 * scale))

    title = text_lines[0] + rng.choice(BRAND_MARKS)
    d.text((panel[0] + 60, H // 2 - 240), title, fill=ink, font=_font(font_path, title_px))
    d.text((panel[0] + 60, H // 2 - 40), text_lines[1], fill=ink,
           font=_font(font_path, sub_px))

    # Ingredient small print: a real feature of packaging and often the thing
    # a user actually wants read.
    tiny = _font("/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
                 max(13, int(H * 0.017 * scale)))
    for i, line in enumerate(
        ["INGREDIENTS: CARBONATED WATER, CITRIC ACID,",
         "NATURAL FLAVOR, ASPARTAME, CAFFEINE.",
         "PHENYLKETONURICS: CONTAINS PHENYLALANINE."]
    ):
        d.text((panel[0] + 60, H // 2 + 90 + i * int(H * 0.026 * scale)),
               line, fill=ink, font=tiny)

    # Symbols crowding the text -- the actual point of this corpus.
    draw_barcode(d, panel[2] - 330, H // 2 + 250, 270, 110, rng)
    draw_recycling(d, panel[0] + 130, H // 2 + 300, 58)
    draw_flourish(d, W // 2 - 190, H // 2 - 300, 380, rng)

    return img


def capture_package(img: Image.Image, rng, curved: bool) -> bytes:
    """Photograph the label: curvature, gloss, hand-held blur."""
    out = _curve(img, rng.uniform(0.7, 1.3)) if curved else img
    out = out.rotate(rng.uniform(-7, 7), resample=Image.BICUBIC, fillcolor="#6b7280")
    out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.5)))

    a = np.asarray(out).astype(np.float32)
    a = (a - 128) * rng.uniform(0.75, 1.0) + 128
    # Specular highlight: packaging is glossy, and the strip washes out glyphs.
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    gx = rng.uniform(0.25, 0.75) * W
    a += (rng.uniform(55, 130) * np.exp(-(((xx - gx) ** 2) / (2 * 110 ** 2))))[..., None]
    a += np.random.normal(0, rng.uniform(4, 10), a.shape)

    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=int(rng.uniform(45, 80)))
    return buf.getvalue()


def build_packaging_corpus(n: int = 24, seed: int = 31, frames: int = 3):
    """Product labels. Truth is the product name, the hardest single line."""
    rng = random.Random(seed)
    np.random.seed(seed)
    samples = []
    for i in range(n):
        lines = rng.choice(PRODUCT_TEXT)
        font = DISPLAY_FONTS[i % len(DISPLAY_FONTS)]
        scale = rng.choice([0.55, 0.75, 1.0, 1.0, 1.3])
        curved = i % 2 == 0
        base = render_package(lines, font, scale, rng)
        samples.append({
            "frames": [capture_package(base, rng, curved) for _ in range(frames)],
            "truth": lines[0],
            "font": os.path.basename(font),
            "curved": curved,
            "scale": scale,
        })
    return samples


def build_symbol_corpus(n: int = 12, seed: int = 37, frames: int = 3):
    """Images of symbols and drawings with NO text anywhere.

    Barcodes, recycling marks, nutrition badges, decorative scrollwork. These
    carry the repeating high-frequency structure that OCR engines resolve into
    glyphs. Any word spoken here was invented.
    """
    rng = random.Random(seed)
    np.random.seed(seed)
    kinds = ["barcode", "recycling", "nutrition", "flourish", "mixed", "grid"]
    out = []

    for i in range(n):
        kind = kinds[i % len(kinds)]
        img = Image.new("RGB", (W, H), rng.choice(["#f4f4f5", "#e7e5e4", "#fafaf9"]))
        d = ImageDraw.Draw(img)

        if kind in ("barcode", "mixed"):
            draw_barcode(d, 260, 380, 1080, 380, rng)
        if kind in ("recycling", "mixed"):
            draw_recycling(d, W // 2, H // 2, 240)
        if kind in ("nutrition", "mixed"):
            draw_nutrition_icons(d, 340, 820, rng)
        if kind in ("flourish", "mixed"):
            for row in range(4):
                draw_flourish(d, 300, 260 + row * 190, 1000, rng)
        if kind == "grid":
            # Dense rule lines: a nutrition table stripped of its words.
            for y in range(240, 980, 62):
                d.line([300, y, 1300, y], fill="#3f3f46", width=4)
            for x in range(300, 1320, 168):
                d.line([x, 240, x, 960], fill="#3f3f46", width=4)

        out.append({
            "frames": [capture_package(img, rng, False) for _ in range(frames)],
            "kind": kind,
        })
    return out
