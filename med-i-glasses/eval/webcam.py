"""What a cheap fixed-focus webcam actually delivers.

The other corpora simulate a phone: Gaussian blur, noise, glare, JPEG. That
is optimistic about the camera this project ships on. A USB webcam clipped to
a pair of glasses fails in two ways a phone does not, and both destroy small
print specifically:

  DEFOCUS. The lens is fixed at roughly half a metre. A label held up to read
  sits at 15-25 cm, well inside the near limit, so the text is genuinely out
  of focus -- not motion-blurred. The difference matters: motion blur smears
  along one axis and leaves stroke edges recoverable, while defocus is a disc
  convolution that fills counters and merges adjacent strokes. Sharpening
  helps the first and cannot undo the second.

  SENSOR RESOLUTION. A webcam advertising 1080p often resolves far less, and
  the frame the browser hands over is what the sensor gave. Rendering a label
  at 1600 px and blurring it models a sharp camera badly downsampled; a real
  webcam never had those pixels. So the pipeline here renders large, then
  *resamples down to the sensor's real resolution*, and everything downstream
  sees only that. Detail lost this way cannot be upscaled back, which is
  exactly the situation on the glasses.

Three tiers, so the cost of the camera is measurable rather than assumed. If
accuracy on `cheap` is far below `phone`, the fix is a better camera, not a
better algorithm -- and that is worth knowing before spending a day on the
algorithm.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter


@dataclass(frozen=True, slots=True)
class Camera:
    """A capture path, worst characteristics first."""

    name: str
    # Longest edge the sensor actually resolves. The single most destructive
    # parameter for small print.
    sensor_px: int
    # Radius of the defocus disc, in pixels at sensor resolution.
    defocus_px: float
    # Sensor read noise, applied before compression as it is in a real camera.
    noise: float
    # MJPEG quality webcams typically deliver.
    jpeg_quality: int
    # Residual hand shake, on top of defocus.
    shake_px: float


# phone: what the earlier corpora implicitly assumed.
PHONE = Camera("phone", sensor_px=1600, defocus_px=0.0, noise=4.0, jpeg_quality=85, shake_px=0.6)
# webcam: a decent 1080p clip-on, autofocus present and roughly right.
WEBCAM = Camera("webcam", sensor_px=1280, defocus_px=1.1, noise=7.0, jpeg_quality=70, shake_px=1.0)
# cheap: fixed focus, label inside the near limit, small noisy sensor. This is
# the one the glasses rig resembles.
CHEAP = Camera("cheap", sensor_px=960, defocus_px=2.4, noise=11.0, jpeg_quality=55, shake_px=1.4)

TIERS = {c.name: c for c in (PHONE, WEBCAM, CHEAP)}


def _defocus(image: Image.Image, radius: float) -> Image.Image:
    """Disc convolution, not Gaussian.

    A defocused lens spreads a point into a filled circle. Gaussian blur
    leaves a bright core that sharpening can pull back; a disc does not, and
    that is why defocused small print is unrecoverable in a way motion blur
    is not.
    """
    if radius < 0.4:
        return image

    size = int(radius * 2) | 1  # odd, so the kernel has a centre
    centre = size // 2
    yy, xx = np.mgrid[0:size, 0:size]
    disc = ((xx - centre) ** 2 + (yy - centre) ** 2) <= radius ** 2
    kernel = disc.astype(np.float32)
    kernel /= kernel.sum()

    return image.filter(
        ImageFilter.Kernel(
            (size, size), kernel.flatten().tolist(), scale=1.0, offset=0
        )
    )


def through_camera(
    image: Image.Image, camera: Camera, rng: random.Random | None = None
) -> bytes:
    """Render a scene as this camera would deliver it."""
    rng = rng or random.Random(0)
    out = image.convert("RGB")

    # Resample to the sensor first. Doing this last would model a sharp
    # capture that was downscaled afterwards, which is a different and much
    # kinder failure.
    longest = max(out.size)
    if longest > camera.sensor_px:
        scale = camera.sensor_px / longest
        out = out.resize(
            (max(1, int(out.width * scale)), max(1, int(out.height * scale))),
            Image.LANCZOS,
        )

    out = _defocus(out, camera.defocus_px)
    if camera.shake_px > 0.2:
        out = out.filter(ImageFilter.GaussianBlur(camera.shake_px * rng.uniform(0.6, 1.0)))

    pixels = np.asarray(out).astype(np.float32)
    pixels += np.random.normal(0, camera.noise, pixels.shape)
    # Cheap sensors lose contrast in the shadows and clip highlights early.
    pixels = (pixels - 128) * rng.uniform(0.86, 1.0) + 128 + rng.uniform(-12, 8)
    out = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))

    buffer = io.BytesIO()
    # 4:2:0 subsampling, as MJPEG delivers; it blurs coloured text edges.
    out.save(buffer, "JPEG", quality=camera.jpeg_quality, subsampling=2)
    return buffer.getvalue()


def burst(
    image: Image.Image, camera: Camera, frames: int = 3, seed: int = 0
) -> list[bytes]:
    """Several frames of the same scene, differing only as a camera would.

    Defocus is a property of the lens and the distance, so it does not vary
    frame to frame -- only shake and noise do. That is why a burst helps less
    on a fixed-focus camera than on a phone, and worth measuring rather than
    assuming.
    """
    rng = random.Random(seed)
    np.random.seed(seed % 2**31)
    return [through_camera(image, camera, rng) for _ in range(frames)]
