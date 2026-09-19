"""OCR evaluation harness.

Measures character error rate on synthetic-but-realistic signage: real system
fonts, varied apparent distance, perspective, lighting and camera artifacts.

CER, not "does the output contain the word". The loose check hid failures --
"Room 204B" read as "Room 2048" passed it.
"""

from __future__ import annotations

import io
import math
import os
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from typefaces import platform_fonts

# Real system fonts on this machine by default. `--fonts bundled` on the
# runner renders with the display faces committed under eval/fonts/ instead,
# which are identical on every machine, so those numbers compare across
# platforms. Platform numbers only compare within one platform.
FONTS = platform_fonts()

# What signage actually says.
PHRASES = [
    "EXIT", "Room 204B", "Keep door closed", "Platform 9", "No Entry",
    "Fire Exit", "Conference Room B", "Capacity 24", "Push", "Pull",
    "Reception", "Stairs", "Elevator", "Restroom", "Gate A12",
    "Baggage Claim", "Departures", "Arrivals", "Emergency Exit",
    "Staff Only", "Wet Floor", "Please Wait Here", "Ticket Office",
    "Information", "Lost Property", "Way Out", "Car Park Level 3",
    "Quiet Zone", "No Smoking", "Meeting Room 7",
]

W, H = 1600, 1200

PALETTES = [
    ("#f4f2ed", "#141414"),   # white sign, black text
    ("#14532d", "#f8fafc"),   # green exit sign
    ("#1e3a8a", "#ffffff"),   # blue wayfinding
    ("#fef08a", "#1c1917"),   # yellow warning
    ("#dc2626", "#ffffff"),   # red emergency
    ("#e7e5e4", "#292524"),   # grey plaque
]


@dataclass
class Sample:
    frames: list[bytes]
    truth: str
    font: str
    height_pct: float


def _load_font(path: str, px: int):
    for candidate in (path, *FONTS):
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)  # Pillow >= 10.1; last resort


def _perspective(img: Image.Image, strength: float) -> Image.Image:
    """Approximate viewing a sign off-axis."""
    w, h = img.size
    dx = int(w * strength)
    dy = int(h * strength * 0.4)
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    dst = [(dx, dy), (w - dx // 2, 0), (w, h - dy), (dx // 3, h)]

    # Solve the 8 coefficients mapping dst -> src.
    matrix = []
    for (x, y), (u, v) in zip(dst, src):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(matrix, dtype=float)
    B = np.array(src, dtype=float).reshape(8)
    coeffs = np.linalg.solve(A, B)
    return img.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC,
                         fillcolor="#8a8580")


def render(text: str, font_path: str, height_pct: float, rng: random.Random) -> Image.Image:
    """A sign photographed from some distance; height_pct is apparent size."""
    bg, fg = rng.choice(PALETTES)
    px = max(8, int(H * height_pct))

    img = Image.new("RGB", (W, H), "#8a8580")
    d = ImageDraw.Draw(img)

    # Cluttered wall behind the sign
    for _ in range(14):
        x0, y0 = rng.randint(0, W), rng.randint(0, H)
        d.rectangle([x0, y0, x0 + rng.randint(40, 300), y0 + rng.randint(30, 200)],
                    fill=(rng.randint(100, 165),) * 3)

    font = _load_font(font_path, px)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    pad = max(12, px // 3)
    cx, cy = W // 2, H // 2
    d.rectangle([cx - tw // 2 - pad, cy - th // 2 - pad,
                 cx + tw // 2 + pad, cy + th // 2 + pad],
                fill=bg, outline="#4b4b4b", width=max(1, px // 30))
    d.text((cx - tw // 2 - bbox[0], cy - th // 2 - bbox[1]), text, fill=fg, font=font)
    return img


def capture(img: Image.Image, rng: random.Random, severity: float) -> bytes:
    """Simulate one hand-held phone frame."""
    out = img
    if severity > 0.25:
        out = _perspective(out, rng.uniform(0.02, 0.05) * severity)
    out = out.rotate(rng.uniform(-6, 6) * severity, resample=Image.BICUBIC,
                     fillcolor="#8a8580")
    out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 2.2) * severity))

    a = np.asarray(out).astype(np.float32)
    a = (a - 128) * rng.uniform(0.62, 0.95) + 128           # flatten contrast
    a += rng.uniform(-38, 18)                                # exposure
    a += np.random.normal(0, rng.uniform(4, 13) * severity, a.shape)

    # Glare blob
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    gx, gy = rng.uniform(0.2, 0.85) * W, rng.uniform(0.15, 0.8) * H
    a += (rng.uniform(30, 105) * severity *
          np.exp(-(((xx - gx) ** 2 + (yy - gy) ** 2) / (2 * 280 ** 2))))[..., None]

    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=int(rng.uniform(38, 74)))
    return buf.getvalue()


def build_corpus(
    n: int = 60, seed: int = 11, frames: int = 3, fonts: list[str] | None = None
) -> list[Sample]:
    rng = random.Random(seed)
    np.random.seed(seed)
    pool = fonts or FONTS
    samples = []
    for _ in range(n):
        text = rng.choice(PHRASES)
        font = rng.choice(pool)
        # 1.2%-14% of frame height: across a room to arm's length.
        height_pct = rng.choice([0.012, 0.018, 0.025, 0.035, 0.05, 0.075, 0.11, 0.14])
        severity = rng.uniform(0.5, 1.25)
        base = render(text, font, height_pct, rng)
        samples.append(Sample(
            frames=[capture(base, rng, severity) for _ in range(frames)],
            truth=text,
            font=os.path.basename(font),
            height_pct=height_pct,
        ))
    return samples


def build_textureless_corpus(n: int = 8, seed: int = 23) -> list[list[bytes]]:
    """Surfaces containing no text at all.

    Carpet, brick, foliage and blinds are what a reader hallucinates on: they
    carry repeating high-frequency structure that looks like glyphs. Any
    output on these is invented, so this measures the failure that matters
    most -- a blind user cannot glance at the wall to check.
    """
    rng = random.Random(seed)
    np.random.seed(seed)
    kinds = ["carpet", "brick", "foliage", "blinds"]
    out = []
    for i in range(n):
        kind = kinds[i % len(kinds)]
        out.append([_texture(kind, rng) for _ in range(3)])
    return out


def _texture(kind: str, rng: random.Random) -> bytes:
    a = np.random.normal(140, 34, (H, W, 3))
    if kind == "carpet":
        a += np.random.normal(0, 26, (H, W, 3))
    elif kind == "brick":
        for y in range(0, H, 74):
            a[y:y + 6, :] = 96
        for x in range(0, W, 155):
            a[:, x:x + 6] = 96
    elif kind == "foliage":
        yy, xx = np.mgrid[0:H, 0:W]
        a += (36 * np.sin(xx / 7.0) * np.cos(yy / 9.0))[..., None]
    elif kind == "blinds":
        for y in range(0, H, 22):
            a[y:y + 9, :] -= 52

    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=int(rng.uniform(50, 68)))
    return buf.getvalue()


# -- metrics ---------------------------------------------------------------

def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(pred: str, truth: str) -> float:
    """Character error rate, capped at 1. Lower is better."""
    if not truth:
        return 0.0
    return min(1.0, edit_distance(pred.lower().strip(), truth.lower().strip()) / len(truth))


def normalize_output(spoken: str) -> str:
    """Strip the 'It reads:' wrapper so only the text is scored."""
    text = spoken.strip()
    for prefix in ("It reads:", "The sign reads:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip().rstrip(".").replace(".", " ").strip()
