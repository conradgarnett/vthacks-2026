"""Surfaces that contain no text but look like they might.

This is the hallucination test, and it is the one metric here that cannot be
gamed by reading better: every word emitted on these images was invented, and
for a blind user an invented ingredient is worse than silence because there
is no way to check it.

WHAT MAKES A SURFACE PROVOKE A READER

Not noise. An OCR detector looks for high-contrast strokes of roughly
consistent height, repeating along a line, separated by gaps -- because that
is what a word is. Gaussian static has no edges at any scale and no detector
fires on it, so a corpus of static measures nothing and scores a clean zero
that means nothing.

The surfaces here are built to have that structure on purpose:

  vertical strokes in a row   railings, fence pickets, book spines, radiator
                              fins, corrugated metal, venetian blinds
  grids and courses           brickwork, tiling, keyboards, pegboard
  correlated organic grain    carpet pile, foliage, wood, gravel, fabric

Book spines and railings are the strongest: a shelf of books is literally
strokes of varying width and colour, evenly spaced, at text height. A reader
that survives those is doing real work.

Everything is drawn from structure first and given only enough noise to look
photographed. The previous version of this file did the reverse -- it started
from `np.random.normal(140, 34)` and added structure of amplitude 26-36 on
top, so the structure sat below the noise floor and every surface came out as
grey mush.
"""

from __future__ import annotations

import io
import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1500, 1150

KINDS = [
    "brick", "carpet", "foliage", "blinds", "tile", "wood", "railings",
    "mesh", "corrugated", "radiator", "paving", "books", "keyboard",
    "shutters", "gravel", "fabric", "pegboard", "clapboard",
]


def _grain(
    width: int, height: int, scale: float, rng: random.Random, amplitude: float = 34
) -> np.ndarray:
    """Correlated noise: static generated small and scaled up, so it has
    edges at the scale that matters rather than at the pixel.

    Returned centred on zero. An earlier version shifted by `small.min()` and
    multiplied before casting to uint8, which overflowed for any bright
    sample and clipped the whole field to white -- the carpet came out as a
    blank sheet and nothing said so.
    """
    small = np.random.normal(
        0, 1, (max(2, int(height / scale)), max(2, int(width / scale)))
    )
    scaled = np.clip(small * amplitude + 128, 0, 255).astype(np.uint8)
    image = Image.fromarray(scaled).resize((width, height), Image.BICUBIC)
    return np.asarray(image).astype(np.float32) - 128


def _shade(array: np.ndarray, rng: random.Random) -> np.ndarray:
    """A lighting gradient, so the surface reads as photographed not printed."""
    height, width = array.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    cx, cy = rng.uniform(0.2, 0.8) * width, rng.uniform(0.2, 0.8) * height
    falloff = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (width * 0.9)
    # [..., None] so the 2-D gradient broadcasts across the colour channels.
    return array * (1.12 - 0.45 * falloff)[..., None]


def _finish(img: Image.Image, rng: random.Random) -> bytes:
    """Camera: slight defocus, sensor noise, exposure drift, JPEG."""
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.4)))
    pixels = np.asarray(img).astype(np.float32)
    pixels += np.random.normal(0, rng.uniform(3, 9), pixels.shape)
    pixels = (pixels - 128) * rng.uniform(0.88, 1.06) + 128 + rng.uniform(-14, 10)
    out = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    buffer = io.BytesIO()
    out.save(buffer, "JPEG", quality=int(rng.uniform(52, 76)), subsampling=2)
    return buffer.getvalue()


def _base(colour: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (W, H), colour)


def _jitter(rng: random.Random, base: int, spread: int) -> int:
    return max(0, min(255, base + rng.randint(-spread, spread)))


def brick(rng):
    img = _base((150, 140, 132))
    d = ImageDraw.Draw(img)
    course = rng.randint(64, 96)
    length = rng.randint(150, 230)
    mortar = rng.randint(7, 13)
    tone = rng.choice([(160, 82, 60), (140, 74, 58), (172, 110, 88), (120, 92, 80)])
    for row, y in enumerate(range(-course, H + course, course)):
        offset = (row % 2) * (length // 2)
        for x in range(-length, W + length, length):
            r, g, b = (_jitter(rng, c, 22) for c in tone)
            d.rectangle(
                [x + offset + mortar, y + mortar, x + offset + length, y + course],
                fill=(r, g, b),
            )
    return img


def carpet(rng):
    tone = rng.choice([(120, 110, 100), (90, 96, 104), (140, 128, 116), (76, 84, 78)])
    pile = _grain(W, H, rng.uniform(2.5, 5.0), rng) * 1.4
    fleck = _grain(W, H, rng.uniform(1.2, 2.0), rng) * 0.9
    stack = np.dstack([
        np.clip(tone[i] + pile + fleck, 0, 255) for i in range(3)
    ])
    return Image.fromarray(stack.astype(np.uint8))


def foliage(rng):
    img = _base((38, 54, 32))
    d = ImageDraw.Draw(img)
    for _ in range(rng.randint(260, 420)):
        cx, cy = rng.randint(0, W), rng.randint(0, H)
        rx, ry = rng.randint(28, 82), rng.randint(14, 42)
        angle = rng.uniform(0, 180)
        green = (
            _jitter(rng, 70, 40), _jitter(rng, 120, 48), _jitter(rng, 58, 34)
        )
        leaf = Image.new("RGBA", (rx * 2, ry * 2), (0, 0, 0, 0))
        ImageDraw.Draw(leaf).ellipse([0, 0, rx * 2 - 1, ry * 2 - 1], fill=green + (235,))
        # A midrib, which is the stroke-like part foliage actually has.
        ImageDraw.Draw(leaf).line(
            [(2, ry), (rx * 2 - 2, ry)],
            fill=tuple(max(0, c - 34) for c in green) + (235,), width=2,
        )
        leaf = leaf.rotate(angle, expand=True)
        img.paste(leaf, (cx - leaf.width // 2, cy - leaf.height // 2), leaf)
    return img


def blinds(rng):
    img = _base((196, 192, 184))
    d = ImageDraw.Draw(img)
    slat = rng.randint(30, 52)
    for y in range(-slat, H + slat, slat):
        shade = _jitter(rng, 206, 16)
        d.rectangle([0, y, W, y + slat - rng.randint(4, 8)], fill=(shade, shade - 4, shade - 10))
        d.rectangle([0, y + slat - rng.randint(4, 8), W, y + slat], fill=(96, 94, 90))
    # The pull cords: two long vertical strokes, which is the glyph-like part.
    for x in (int(W * 0.24), int(W * 0.76)):
        d.line([(x, 0), (x, H)], fill=(150, 146, 138), width=rng.randint(4, 8))
    return img


def tile(rng):
    img = _base((88, 86, 84))
    d = ImageDraw.Draw(img)
    size = rng.randint(96, 168)
    grout = rng.randint(6, 12)
    tone = rng.choice([(226, 224, 218), (198, 206, 208), (170, 168, 160), (238, 232, 220)])
    for y in range(-size, H + size, size):
        for x in range(-size, W + size, size):
            c = tuple(_jitter(rng, v, 14) for v in tone)
            d.rectangle([x + grout, y + grout, x + size, y + size], fill=c)
    return img


def wood(rng):
    tone = rng.choice([(150, 110, 70), (112, 82, 54), (178, 140, 96)])
    yy, xx = np.mgrid[0:H, 0:W]
    warp = np.sin(xx / rng.uniform(180, 340) + yy / rng.uniform(600, 1100)) * 30
    rings = np.sin((yy + warp) / rng.uniform(5, 11)) * rng.uniform(14, 26)
    grain = _grain(W, H, 2.0, rng) * 0.6
    stack = np.dstack([
        np.clip(tone[i] + rings + grain, 0, 255) for i in range(3)
    ])
    return Image.fromarray(stack.astype(np.uint8))


def railings(rng):
    """Vertical bars in a row. The strongest false-text surface there is."""
    img = _base(rng.choice([(150, 160, 170), (96, 104, 112), (186, 182, 176)]))
    d = ImageDraw.Draw(img)
    pitch = rng.randint(42, 90)
    width = rng.randint(9, 22)
    bar = rng.choice([(40, 44, 48), (70, 66, 60), (28, 30, 34)])
    for x in range(-pitch, W + pitch, pitch):
        c = tuple(_jitter(rng, v, 16) for v in bar)
        d.rectangle([x, 0, x + width, H], fill=c)
        d.line([(x + width, 0), (x + width, H)], fill=(190, 190, 190), width=2)
    for y in (int(H * 0.2), int(H * 0.78)):
        d.rectangle([0, y, W, y + rng.randint(16, 30)], fill=bar)
    return img


def mesh(rng):
    img = _base((120, 126, 130))
    d = ImageDraw.Draw(img)
    pitch = rng.randint(34, 62)
    wire = (196, 198, 200)
    for x in range(-H, W + H, pitch):
        d.line([(x, 0), (x + H, H)], fill=wire, width=rng.randint(3, 6))
        d.line([(x, H), (x + H, 0)], fill=wire, width=rng.randint(3, 6))
    return img


def corrugated(rng):
    tone = rng.choice([(168, 172, 176), (140, 128, 110), (110, 120, 128)])
    yy, xx = np.mgrid[0:H, 0:W]
    ribs = np.sin(xx / rng.uniform(6, 16)) * rng.uniform(34, 56)
    stack = np.dstack([np.clip(tone[i] + ribs, 0, 255) for i in range(3)])
    img = Image.fromarray(stack.astype(np.uint8))
    if rng.random() < 0.5:  # streaks of rust, which break the regularity
        d = ImageDraw.Draw(img)
        for _ in range(rng.randint(4, 12)):
            x = rng.randint(0, W)
            d.line([(x, rng.randint(0, H // 2)), (x, H)],
                   fill=(rng.randint(110, 150), 70, 40), width=rng.randint(3, 14))
    return img


def radiator(rng):
    img = _base((228, 226, 220))
    d = ImageDraw.Draw(img)
    pitch = rng.randint(26, 46)
    for x in range(0, W, pitch):
        shade = _jitter(rng, 206, 18)
        d.rectangle([x, 0, x + pitch - rng.randint(6, 12), H], fill=(shade, shade, shade - 6))
        d.line([(x, 0), (x, H)], fill=(150, 148, 144), width=2)
    d.rectangle([0, 0, W, rng.randint(40, 90)], fill=(236, 234, 228))
    d.rectangle([0, H - rng.randint(40, 90), W, H], fill=(236, 234, 228))
    return img


def paving(rng):
    img = _base((78, 76, 74))
    d = ImageDraw.Draw(img)
    y = -60
    while y < H + 60:
        depth = rng.randint(80, 150)
        x = -60
        while x < W + 60:
            width = rng.randint(110, 240)
            c = _jitter(rng, 168, 30)
            d.rectangle([x + 5, y + 5, x + width, y + depth],
                        fill=(c, c - rng.randint(0, 8), c - rng.randint(0, 14)))
            x += width
        y += depth
    return img


def books(rng):
    """A shelf of spines: strokes of varying width and colour, at text height."""
    img = _base((62, 50, 40))
    d = ImageDraw.Draw(img)
    shelves = rng.randint(2, 4)
    band = H // shelves
    for s in range(shelves):
        top = s * band
        bottom = top + band - rng.randint(18, 38)
        x = rng.randint(-40, 10)
        while x < W:
            width = rng.randint(22, 72)
            colour = (rng.randint(30, 210), rng.randint(30, 200), rng.randint(30, 190))
            height_loss = rng.randint(0, band // 5)
            d.rectangle([x, top + height_loss, x + width, bottom], fill=colour)
            # The band across a spine where the title sits: high contrast,
            # text height, exactly what a detector reaches for.
            if width > 34 and rng.random() < 0.6:
                by = top + height_loss + (bottom - top - height_loss) // 3
                d.rectangle([x + 3, by, x + width - 3, by + rng.randint(12, 26)],
                            fill=(235, 225, 200) if sum(colour) < 330 else (40, 34, 28))
            x += width + rng.randint(1, 5)
        d.rectangle([0, bottom, W, bottom + rng.randint(18, 38)], fill=(92, 72, 54))
    return img


def keyboard(rng):
    img = _base((38, 38, 40))
    d = ImageDraw.Draw(img)
    size = rng.randint(78, 124)
    gap = rng.randint(8, 16)
    for row, y in enumerate(range(20, H, size + gap)):
        offset = int((row % 3) * size * 0.22)
        for x in range(20 + offset, W, size + gap):
            c = _jitter(rng, 64, 12)
            d.rounded_rectangle([x, y, x + size, y + size], radius=8,
                                fill=(c, c, c + 3))
            # The legend on a key is a mark at glyph scale; a blank grid is
            # a much easier test than a real keyboard.
            d.rectangle([x + size // 3, y + size // 3,
                         x + size // 3 + rng.randint(6, 18),
                         y + size // 3 + rng.randint(10, 22)],
                        fill=(200, 200, 205))
    return img


def shutters(rng):
    img = _base((96, 104, 96))
    d = ImageDraw.Draw(img)
    pitch = rng.randint(30, 54)
    tone = rng.choice([(180, 186, 176), (120, 96, 72), (86, 96, 104)])
    for x in range(-pitch, W + pitch, pitch):
        c = tuple(_jitter(rng, v, 14) for v in tone)
        d.polygon([(x, 0), (x + pitch - 8, 0), (x + pitch - 2, H), (x + 6, H)], fill=c)
        # A dark gap between slats. Without it the slats sit a few levels
        # apart and the whole surface washes out to one tone, which is the
        # failure this corpus exists to avoid.
        d.line([(x + pitch - 5, 0), (x + pitch + 1, H)],
               fill=tuple(max(0, v - 70) for v in tone), width=rng.randint(4, 9))
    return img


def gravel(rng):
    img = _base((92, 90, 86))
    d = ImageDraw.Draw(img)
    for _ in range(rng.randint(1400, 2600)):
        cx, cy = rng.randint(0, W), rng.randint(0, H)
        r = rng.randint(6, 22)
        c = _jitter(rng, 150, 46)
        d.ellipse([cx - r, cy - int(r * rng.uniform(0.6, 1.0)),
                   cx + r, cy + int(r * rng.uniform(0.6, 1.0))],
                  fill=(c, c - rng.randint(0, 10), c - rng.randint(0, 16)))
    return img


def fabric(rng):
    tone = rng.choice([(140, 120, 100), (90, 100, 120), (170, 160, 150)])
    yy, xx = np.mgrid[0:H, 0:W]
    pitch = rng.uniform(4, 9)
    weave = (np.sin(xx / pitch) * np.sin(yy / pitch)) * rng.uniform(22, 38)
    grain = _grain(W, H, 3.0, rng) * 0.5
    stack = np.dstack([np.clip(tone[i] + weave + grain, 0, 255) for i in range(3)])
    return Image.fromarray(stack.astype(np.uint8))


def pegboard(rng):
    img = _base(rng.choice([(196, 168, 128), (180, 180, 176)]))
    d = ImageDraw.Draw(img)
    pitch = rng.randint(40, 70)
    r = rng.randint(6, 12)
    for y in range(pitch, H, pitch):
        for x in range(pitch, W, pitch):
            d.ellipse([x - r, y - r, x + r, y + r], fill=(60, 52, 42))
    return img


def clapboard(rng):
    img = _base((206, 202, 192))
    d = ImageDraw.Draw(img)
    board = rng.randint(56, 96)
    tone = rng.choice([(226, 222, 212), (150, 160, 150), (188, 176, 152)])
    for y in range(-board, H + board, board):
        c = tuple(_jitter(rng, v, 10) for v in tone)
        d.rectangle([0, y, W, y + board - 6], fill=c)
        d.rectangle([0, y + board - 6, W, y + board], fill=(120, 116, 108))
    return img


BUILDERS = {
    "brick": brick, "carpet": carpet, "foliage": foliage, "blinds": blinds,
    "tile": tile, "wood": wood, "railings": railings, "mesh": mesh,
    "corrugated": corrugated, "radiator": radiator, "paving": paving,
    "books": books, "keyboard": keyboard, "shutters": shutters,
    "gravel": gravel, "fabric": fabric, "pegboard": pegboard,
    "clapboard": clapboard,
}


def render(kind: str, rng: random.Random) -> Image.Image:
    img = BUILDERS[kind](rng)
    array = _shade(np.asarray(img).astype(np.float32), rng)
    img = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
    # A little perspective, since none of these is ever seen square on.
    if rng.random() < 0.6:
        shift = rng.uniform(0.02, 0.10)
        img = img.transform(
            (W, H), Image.AFFINE,
            (1, rng.uniform(-shift, shift), 0, rng.uniform(-shift, shift) * 0.4, 1, 0),
            resample=Image.BICUBIC,
        )
    return img


def texture(kind: str, rng: random.Random) -> bytes:
    return _finish(render(kind, rng), rng)


def build_textureless_corpus(
    n: int = 36, seed: int = 23, frames: int = 3
) -> list[list[bytes]]:
    """Surfaces with no text on them at all.

    Any output on these was invented. This is the one score here that cannot
    be improved by reading better, only by knowing when not to read.
    """
    rng = random.Random(seed)
    np.random.seed(seed % 2**31)
    out = []
    for i in range(n):
        kind = KINDS[i % len(KINDS)]
        out.append([texture(kind, rng) for _ in range(frames)])
    return out


def build_labelled(n: int = 36, seed: int = 23, frames: int = 3) -> list[dict]:
    """The same corpus, with the surface named, for reporting by kind."""
    rng = random.Random(seed)
    np.random.seed(seed % 2**31)
    out = []
    for i in range(n):
        kind = KINDS[i % len(KINDS)]
        out.append({
            "frames": [texture(kind, rng) for _ in range(frames)],
            "kind": kind,
            "no_text": True,
        })
    return out
