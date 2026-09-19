"""Font benchmark: typeface as the only variable.

The signage and packaging corpora vary distance, lighting, curvature and font
all at once, which is realistic but makes it impossible to say whether a
failure belongs to the typeface or to the conditions. This holds everything
else fixed -- flat surface, good light, consistent size -- and changes only
the face, so a bad score is a fact about the font.

Real-world text is mostly not Helvetica. Product names, shop fronts, menus and
posters are set in display and script faces, and those are exactly where a
reader trained on clean signage falls over.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1400, 500

# Grouped by how a reader tends to fail on them.
FONT_FAMILIES: dict[str, list[str]] = {
    "grotesque": [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Verdana.ttf",
        "/System/Library/Fonts/Supplemental/Tahoma.ttf",
        "/System/Library/Fonts/Avenir.ttc",
        "/System/Library/Fonts/Supplemental/Futura.ttc",
    ],
    "serif": [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Baskerville.ttc",
        "/System/Library/Fonts/Supplemental/Palatino.ttc",
        "/System/Library/Fonts/Supplemental/Didot.ttc",
        "/System/Library/Fonts/Supplemental/Bodoni 72.ttc",
    ],
    "slab_mono": [
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/System/Library/Fonts/Supplemental/American Typewriter.ttc",
        "/System/Library/Fonts/Supplemental/Rockwell.ttc",
        "/System/Library/Fonts/Menlo.ttc",
    ],
    "condensed_display": [
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
        "/System/Library/Fonts/Supplemental/Copperplate.ttc",
        "/System/Library/Fonts/Supplemental/Optima.ttc",
        "/System/Library/Fonts/Supplemental/Gill Sans.ttc",
    ],
    "script_handwriting": [
        "/System/Library/Fonts/Supplemental/Brush Script.ttf",
        "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
        "/System/Library/Fonts/Supplemental/Marker Felt.ttc",
        "/System/Library/Fonts/Supplemental/Chalkduster.ttf",
        "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc",
        "/System/Library/Fonts/Apple Chancery.ttf",
    ],
    "decorative": [
        "/System/Library/Fonts/Supplemental/Papyrus.ttc",
        "/System/Library/Fonts/Supplemental/Trattatello.ttf",
        "/System/Library/Fonts/Supplemental/Herculanum.ttf",
        "/System/Library/Fonts/Supplemental/Luminari.ttf",
        "/System/Library/Fonts/Supplemental/Zapfino.ttf",
    ],
}

PHRASES = ["Diet Cola", "Fire Exit", "Room 204B", "Ginger Ale", "Reception"]

_BUNDLED_DIR = Path(__file__).resolve().parent / "fonts"

# Open-licensed faces committed under eval/fonts/, chosen for what a person
# actually meets: shopfronts, menus, packaging, cafe boards. They render
# identically on every machine, so scores compare across a Mac and a Windows
# laptop in a way the per-platform system fonts cannot.
#
# Cursive is over-represented on purpose. It is both the commonest hard case
# in daily life -- drinks, cosmetics, bakeries, greeting cards -- and the one
# an OCR engine fails worst on.
BUNDLED_FAMILIES: dict[str, list[str]] = {
    "everyday_sans": [
        "Montserrat-Variable.ttf", "OpenSans-Variable.ttf", "Lato-Regular.ttf",
        "Poppins-Regular.ttf", "Oswald-Variable.ttf", "Raleway-Variable.ttf",
        "Anton-Regular.ttf", "BebasNeue-Regular.ttf", "Righteous-Regular.ttf",
    ],
    "everyday_serif": [
        "PlayfairDisplay-Variable.ttf", "Lora-Variable.ttf",
        "PT_Serif-Web-Regular.ttf", "CinzelDecorative-Regular.ttf",
    ],
    "cursive_script": [
        "DancingScript-Variable.ttf", "GreatVibes-Regular.ttf",
        "Sacramento-Regular.ttf", "Courgette-Regular.ttf",
        "Parisienne-Regular.ttf", "Allura-Regular.ttf", "Cookie-Regular.ttf",
        "AlexBrush-Regular.ttf", "Tangerine-Regular.ttf",
        "MarckScript-Regular.ttf", "KaushanScript-Regular.ttf",
        "Pacifico-Regular.ttf", "Satisfy-Regular.ttf", "Lobster-Regular.ttf",
    ],
    "handwriting": [
        "Caveat-Variable.ttf", "IndieFlower-Regular.ttf",
        "PatrickHand-Regular.ttf", "RockSalt-Regular.ttf",
        "PermanentMarker-Regular.ttf", "AmaticSC-Regular.ttf",
    ],
    "novelty_display": [
        "Bangers-Regular.ttf", "BlackOpsOne-Regular.ttf",
        "Creepster-Regular.ttf", "Monoton-Regular.ttf",
        "SpecialElite-Regular.ttf",
    ],
}


def bundled_families() -> dict[str, list[str]]:
    """Family -> absolute paths, skipping any face not present on disk."""
    out: dict[str, list[str]] = {}
    for family, names in BUNDLED_FAMILIES.items():
        paths = [str(_BUNDLED_DIR / n) for n in names if (_BUNDLED_DIR / n).exists()]
        if paths:
            out[family] = paths
    return out


def _font(path: str, px: int):
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        return None


def render_phrase(text: str, font_path: str, px: int = 120) -> Image.Image | None:
    font = _font(font_path, px)
    if font is None:
        return None

    img = Image.new("RGB", (W, H), "#f5f4f0")
    d = ImageDraw.Draw(img)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if tw > W - 60:  # some display faces are very wide
        return render_phrase(text, font_path, int(px * 0.7)) if px > 50 else None

    d.text(((W - tw) // 2 - bbox[0], (H - th) // 2 - bbox[1]), text,
           fill="#111111", font=font)
    return img


def capture(img: Image.Image, rng: random.Random, severity: float = 0.6) -> bytes:
    """Mild, realistic capture. Kept gentle so the font is what is tested."""
    out = img.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, fillcolor="#f5f4f0")
    out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.0) * severity))

    a = np.asarray(out).astype(np.float32)
    a = (a - 128) * rng.uniform(0.82, 1.0) + 128
    a += np.random.normal(0, rng.uniform(3, 7), a.shape)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=int(rng.uniform(65, 88)))
    return buf.getvalue()


def build_font_corpus(seed: int = 41, frames: int = 3, phrases_per_font: int = 2,
                      bundled: bool = False):
    """One sample per (font, phrase). Conditions held constant throughout.

    bundled=True uses the open-licensed faces committed under eval/fonts/,
    which render identically everywhere and so compare across machines.
    """
    rng = random.Random(seed)
    np.random.seed(seed)

    families = bundled_families() if bundled else FONT_FAMILIES
    samples = []
    for family, paths in families.items():
        for index, path in enumerate(paths):
            for i in range(phrases_per_font):
                # Positional, not hash-based: Python randomises string
                # hashing per process, so hash(path) silently changed
                # which phrases each font was tested on between runs and
                # made before/after comparisons meaningless.
                text = PHRASES[(index + i) % len(PHRASES)]
                base = render_phrase(text, path)
                if base is None:
                    continue
                samples.append({
                    "frames": [capture(base, rng) for _ in range(frames)],
                    "truth": text,
                    "font": path.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                    "family": family,
                })
    return samples
