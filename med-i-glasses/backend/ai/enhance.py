"""Image variants for text an engine cannot read as-is.

Cursive is the case that forced this. Faces like Great Vibes, Pacifico and
Parisienne return *nothing* from the recognizer -- not garbled text, nothing
-- so no amount of post-processing helps. The only remaining lever is to
change the image before it reaches the engine.

Three properties of cursive are attackable without knowing what the text says:

  slant      cursive leans, typically 10-25 degrees. Shearing it upright makes
             the vertical strokes vertical, which is what segmentation keys on.
  stroke     script faces are thin and connected. Thickening darkens the
             strokes so they survive blur, compression and downscaling.
  contrast   a script on a photographed label often sits at low local
             contrast; binarizing separates ink from surface.

Curvature gets its own variant for packaging, where a cylindrical label bends
the baseline and every glyph with it.

Each variant is a guess, so this is deliberately gated: it only runs when the
ordinary read already failed, and the result still has to pass the same
plausibility filter as any other read. A variant that produces confident
nonsense is worse than the nothing it replaced.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

# Typical cursive slant. Both directions are tried because some faces are
# upright and the engine's own deskew can overshoot.
_SHEAR_ANGLES = (-0.30, -0.18, 0.18)
# Vertical squash applied with de-slanting. Elongated script faces
# (Tangerine, Great Vibes) have tall ascenders that push the x-height small,
# and compressing the line brings the body back toward the proportions the
# recognizer expects.
_SQUASH = 0.82


def _load(frame_jpeg: bytes):
    from PIL import Image

    image = Image.open(io.BytesIO(frame_jpeg))
    image.load()
    return image.convert("L")


def _encode(image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=94)
    return buffer.getvalue()


def _deslant(image, shear: float, squash: float = 1.0):
    """Shear the image upright, optionally compressing it vertically."""
    from PIL import Image

    width, height = image.size
    # Widen the canvas so the shear does not push glyphs off the edge.
    pad = int(abs(shear) * height) + 8
    canvas = Image.new("L", (width + pad * 2, height), color=_background(image))
    canvas.paste(image, (pad, 0))

    sheared = canvas.transform(
        canvas.size,
        Image.AFFINE,
        (1, shear, -shear * height / 2, 0, 1, 0),
        resample=Image.BICUBIC,
        fillcolor=_background(image),
    )
    if squash != 1.0:
        sheared = sheared.resize(
            (sheared.width, max(8, int(sheared.height * squash))), Image.LANCZOS
        )
    return sheared


def _background(image) -> int:
    """Median border intensity, so padding matches the surface."""
    import numpy as np

    pixels = np.asarray(image)
    if pixels.size == 0:
        return 255
    border = np.concatenate([
        pixels[0, :], pixels[-1, :], pixels[:, 0], pixels[:, -1],
    ])
    return int(np.median(border))


def _thicken(image, radius: int = 3):
    """Darken and fatten strokes. MinFilter erodes light, growing dark ink."""
    from PIL import ImageFilter

    return image.filter(ImageFilter.MinFilter(radius))


def _binarize(image):
    """Two-class split at the midpoint between ink and surface.

    A global threshold is enough here because these variants only run on a
    close crop of a label, not a whole room.
    """
    import numpy as np
    from PIL import Image

    pixels = np.asarray(image).astype(np.float32)
    # Otsu, written out: pick the threshold minimising within-class variance.
    histogram, _ = np.histogram(pixels, bins=256, range=(0, 256))
    total = pixels.size
    sum_all = float((np.arange(256) * histogram).sum())
    best_threshold, best_variance = 128, -1.0
    weight_bg = sum_bg = 0.0

    for level in range(256):
        weight_bg += histogram[level]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += level * histogram[level]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_all - sum_bg) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if between > best_variance:
            best_variance, best_threshold = between, level

    binary = (pixels > best_threshold) * 255
    return Image.fromarray(binary.astype("uint8"))


def _unwarp(image, strength: float = 0.5):
    """Flatten a curved baseline, as on a bottle or can.

    Columns are shifted vertically by a parabola, the inverse of how a
    cylindrical label bows text away from the centre.
    """
    import numpy as np
    from PIL import Image

    pixels = np.asarray(image)
    height, width = pixels.shape[:2]
    out = np.full_like(pixels, _background(image))

    for x in range(width):
        u = (x / max(1, width - 1)) * 2 - 1
        shift = int(strength * height * 0.08 * (u * u))
        if shift <= 0:
            out[:, x] = pixels[:, x]
        else:
            out[: height - shift, x] = pixels[shift:, x]
    return Image.fromarray(out)


def variants(frame_jpeg: bytes) -> list[tuple[str, bytes]]:
    """Enhanced versions of a frame, cheapest and most likely first.

    Named so a failure can be traced to the transform that produced it.
    """
    try:
        grey = _load(frame_jpeg)
    except Exception:
        log.debug("enhance: could not decode frame")
        return []

    out: list[tuple[str, bytes]] = []
    try:
        out.append(("thicken", _encode(_thicken(grey))))
        out.append(("binarize", _encode(_binarize(grey))))
        for shear in _SHEAR_ANGLES:
            out.append((f"deslant{shear:+.2f}", _encode(_deslant(grey, shear))))
        # Slant plus squash: the combination that targets elongated script.
        out.append(("deslant_squash", _encode(_deslant(grey, -0.24, _SQUASH))))
        # Thickened *after* de-slanting, so thin connected strokes survive the
        # resample that shearing costs them.
        out.append(("deslant_thicken", _encode(_thicken(_deslant(grey, -0.24)))))
        out.append(("unwarp", _encode(_unwarp(grey))))
        out.append(("unwarp_thicken", _encode(_thicken(_unwarp(grey)))))
    except Exception:
        log.exception("enhance: variant generation failed")

    return out
