"""Prescription and medicine labels -- the highest-stakes reading task.

Misreading a street sign costs a wrong turn. Misreading "take 2 tablets" as
"take 3" can hurt someone, and the user cannot check it. So this corpus
scores the dosage line separately from everything else: a label where the
drug name reads perfectly and the directions read wrong is a failure, not a
partial success.

Everything here is what makes pharmacy labels hard in practice:

  amber plastic  the standard vial tint sits behind the label edges and
                 drags local contrast down
  curvature      a 30mm vial wraps text through a sharp arc
  density        eight to twelve fields crammed into a few square centimetres
  small type     directions are often set smaller than the drug name
  auxiliary      yellow and red warning stickers partly overlapping the label

Held-out samples are generated from a disjoint seed range and never used for
tuning, so a score on them measures reading rather than fitting.
"""

from __future__ import annotations

import io
import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1500, 1150

PHARMACIES = ["CITY PHARMACY", "GREENLEAF DRUGS", "UNIVERSITY RX", "OAKMONT CHEMIST"]

# Generic names and plausible strengths. Deliberately varied in length and
# letter shape -- "Levothyroxine" and "Lisinopril" fail differently.
DRUGS = [
    ("AMOXICILLIN", "500 MG"), ("LISINOPRIL", "10 MG"),
    ("METFORMIN HCL", "850 MG"), ("ATORVASTATIN", "20 MG"),
    ("LEVOTHYROXINE", "75 MCG"), ("OMEPRAZOLE", "40 MG"),
    ("SERTRALINE", "50 MG"), ("IBUPROFEN", "600 MG"),
    ("AMLODIPINE", "5 MG"), ("PREDNISONE", "20 MG"),
    ("GABAPENTIN", "300 MG"), ("AZITHROMYCIN", "250 MG"),
]

# The safety-critical field. Scored on its own.
DIRECTIONS = [
    "TAKE 1 TABLET BY MOUTH DAILY",
    "TAKE 2 TABLETS BY MOUTH TWICE DAILY",
    "TAKE 1 CAPSULE EVERY 8 HOURS",
    "TAKE 1 TABLET EVERY 12 HOURS",
    "TAKE 2 CAPSULES BY MOUTH AT BEDTIME",
    "TAKE 1 TABLET THREE TIMES DAILY WITH FOOD",
    "TAKE HALF TABLET BY MOUTH EVERY MORNING",
]

WARNINGS = [
    "MAY CAUSE DROWSINESS",
    "TAKE WITH FOOD",
    "DO NOT DRINK ALCOHOL",
    "FINISH ALL MEDICATION",
    "AVOID SUNLIGHT",
]

NAMES = ["J. MARTINEZ", "A. OKAFOR", "S. NGUYEN", "R. PATEL", "L. ANDERSSON"]

_LABEL_FONT = "/System/Library/Fonts/Supplemental/Arial Narrow.ttf"
_BOLD_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _font(path: str, px: int):
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", px)


def _curve_label(img: Image.Image, strength: float) -> Image.Image:
    """Wrap the label around a vial: vertical bow plus edge compression."""
    source = np.asarray(img)
    out = np.zeros_like(source)
    height, width = source.shape[:2]

    for x in range(width):
        u = (x / max(1, width - 1)) * 2 - 1
        shift = int(strength * height * 0.09 * (u * u))
        src_x = int(
            (math.asin(max(-1.0, min(1.0, u * 0.94))) / (math.pi / 2)) * 0.5 * (width - 1)
            + 0.5 * (width - 1)
        )
        src_x = max(0, min(width - 1, src_x))
        column = source[:, src_x]
        if shift > 0:
            out[shift:, x] = column[: height - shift]
            out[:shift, x] = column[0]
        else:
            out[:, x] = column
    return Image.fromarray(out)


def render_label(spec: dict, rng: random.Random) -> Image.Image:
    """A pharmacy label as printed: dense, small, monochrome on white."""
    scale = spec["scale"]
    img = Image.new("RGB", (W, H), "#8d8378")  # bench / counter behind
    d = ImageDraw.Draw(img)

    panel = [140, 170, W - 140, H - 170]
    d.rectangle(panel, fill="#fbfaf7", outline="#9a9a9a", width=2)

    left = panel[0] + 34
    y = panel[1] + 26
    base = int(26 * scale)

    d.text((left, y), spec["pharmacy"], fill="#111", font=_font(_BOLD_FONT, int(base * 1.15)))
    y += int(base * 1.7)
    d.text((left, y), f"Rx {spec['rx']}", fill="#333", font=_font(_LABEL_FONT, base))
    d.text((left + 330, y), spec["patient"], fill="#333", font=_font(_LABEL_FONT, base))
    y += int(base * 1.8)

    # Drug name and strength: the largest text on the label.
    d.text((left, y), f"{spec['drug']} {spec['strength']}", fill="#000",
           font=_font(_BOLD_FONT, int(base * 1.55)))
    y += int(base * 2.4)

    # Directions: the field that matters most, and often not the biggest.
    for line in _wrap(spec["directions"], 34):
        d.text((left, y), line, fill="#000", font=_font(_BOLD_FONT, int(base * 1.12)))
        y += int(base * 1.45)

    y += int(base * 0.6)
    d.text((left, y), f"QTY {spec['qty']}    REFILLS {spec['refills']}",
           fill="#333", font=_font(_LABEL_FONT, base))
    y += int(base * 1.5)
    d.text((left, y), f"DISCARD AFTER {spec['expiry']}", fill="#333",
           font=_font(_LABEL_FONT, int(base * 0.92)))

    # Auxiliary warning sticker, often partly over the printed label.
    sticker_y = panel[3] - int(base * 3.4)
    colour = rng.choice(["#f5d90a", "#f28b20", "#e8453c"])
    d.rectangle([left - 10, sticker_y, left + int(430 * scale), sticker_y + int(base * 2.2)],
                fill=colour)
    d.text((left + 6, sticker_y + int(base * 0.45)), spec["warning"], fill="#1a1a1a",
           font=_font(_BOLD_FONT, int(base * 0.95)))

    return img


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def capture_vial(img: Image.Image, rng: random.Random, amber: bool) -> bytes:
    """Photograph the vial: curvature, amber tint, gloss, hand-held blur."""
    out = _curve_label(img, rng.uniform(0.6, 1.4))
    out = out.rotate(rng.uniform(-8, 8), resample=Image.BICUBIC, fillcolor="#8d8378")
    out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.6)))

    a = np.asarray(out).astype(np.float32)
    if amber:
        # Standard amber vial: cuts blue hard, which flattens ink contrast.
        a[:, :, 2] *= rng.uniform(0.55, 0.75)
        a[:, :, 1] *= rng.uniform(0.85, 0.95)
    a = (a - 128) * rng.uniform(0.7, 0.95) + 128
    a += rng.uniform(-25, 15)

    # Specular strip down the curve of the plastic.
    yy, xx = np.mgrid[0 : a.shape[0], 0 : a.shape[1]]
    gx = rng.uniform(0.2, 0.8) * W
    a += (rng.uniform(45, 115) * np.exp(-(((xx - gx) ** 2) / (2 * 95 ** 2))))[..., None]
    a += np.random.normal(0, rng.uniform(4, 11), a.shape)

    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    buffer = io.BytesIO()
    out.save(buffer, "JPEG", quality=int(rng.uniform(42, 78)))
    return buffer.getvalue()


def _spec(rng: random.Random) -> dict:
    drug, strength = rng.choice(DRUGS)
    return {
        "pharmacy": rng.choice(PHARMACIES),
        "patient": rng.choice(NAMES),
        "rx": f"{rng.randint(1000000, 9999999)}",
        "drug": drug,
        "strength": strength,
        "directions": rng.choice(DIRECTIONS),
        "warning": rng.choice(WARNINGS),
        "qty": rng.choice([14, 20, 28, 30, 60, 90]),
        "refills": rng.randint(0, 5),
        "expiry": f"{rng.randint(1, 12):02d}/{rng.randint(26, 28)}",
        "scale": rng.choice([0.7, 0.85, 1.0, 1.0, 1.25]),
    }


def build_medicine_corpus(n: int = 40, seed: int = 101, frames: int = 3):
    """Prescription labels. Each sample carries the fields scored separately."""
    rng = random.Random(seed)
    np.random.seed(seed % 2**31)

    samples = []
    for i in range(n):
        spec = _spec(rng)
        base = render_label(spec, rng)
        amber = i % 3 != 0  # most vials are amber; some are clear or white
        samples.append({
            "frames": [capture_vial(base, rng, amber) for _ in range(frames)],
            "drug": spec["drug"],
            "strength": spec["strength"],
            "directions": spec["directions"],
            "warning": spec["warning"],
            "amber": amber,
            "scale": spec["scale"],
        })
    return samples


def build_holdout_corpus(n: int = 40, frames: int = 3):
    """Disjoint seed range, never used while tuning.

    Thresholds get fitted to whatever they are measured against. A score here
    is the only one that says anything about unseen labels.
    """
    return build_medicine_corpus(n=n, seed=90210, frames=frames)
