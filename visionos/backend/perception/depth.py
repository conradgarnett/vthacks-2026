"""Monocular depth: relative ordering and drop-off detection.

This does NOT produce metric distance -- detector.py owns that, via known
object heights. Depth earns its place doing two things size priors cannot:

  1. Ordering objects the detector has no height prior for.
  2. Finding floor discontinuities -- stairs, curbs, drop-offs -- which have
     no bounding box at all and are the most dangerous thing in the room.

Output is normalized inverse depth: larger means nearer.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import partial

import numpy as np

from backend.config import Settings
from backend.perception.geometry import BoundingBox, pixel_to_azimuth

log = logging.getLogger(__name__)

# Fraction of frame height treated as floor. Drop-offs are looked for here.
_FLOOR_BAND = 0.35
# Inverse-depth jump across adjacent floor rows that implies an edge. Tuned to
# fire on a step down, not on a rug or a shadow.
_DROPOFF_DELTA = 0.18


@dataclass(slots=True)
class DropoffHint:
    """A suspected floor discontinuity. Always reported as uncertain."""

    azimuth_deg: float
    severity: float


class DepthEstimator:
    """Thread-pooled depth model."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pipe = None
        self._device = -1

    def load(self) -> None:
        if self._pipe is not None:
            return
        import torch
        from transformers import pipeline

        if self._settings.perception_device in ("auto", "mps") and torch.backends.mps.is_available():
            device = "mps"
        elif self._settings.perception_device in ("auto", "cuda") and torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

        self._pipe = pipeline(
            "depth-estimation", model=self._settings.depth_model, device=device
        )
        log.info("Depth model loaded on %s", device)

    def estimate_sync(self, image) -> np.ndarray:
        """Return normalized inverse depth in [0, 1]; larger is nearer."""
        if self._pipe is None:
            self.load()

        result = self._pipe(image)
        depth = np.asarray(result["depth"], dtype=np.float32)

        low, high = float(depth.min()), float(depth.max())
        if high - low < 1e-6:
            return np.zeros_like(depth)
        return (depth - low) / (high - low)

    async def estimate(self, image) -> np.ndarray:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self.estimate_sync, image))


def relative_depth_in_box(depth: np.ndarray, box: BoundingBox) -> float:
    """Median inverse depth inside a box.

    Median, not mean: a bounding box always contains some background, and the
    mean drags toward it.
    """
    height, width = depth.shape[:2]
    x1 = max(0, min(int(box.x1), width - 1))
    x2 = max(x1 + 1, min(int(box.x2), width))
    y1 = max(0, min(int(box.y1), height - 1))
    y2 = max(y1 + 1, min(int(box.y2), height))

    patch = depth[y1:y2, x1:x2]
    return float(np.median(patch)) if patch.size else 0.0


def find_dropoffs(
    depth: np.ndarray, hfov_deg: float, columns: int = 12
) -> list[DropoffHint]:
    """Scan the floor band for depth discontinuities.

    Coarse by design. This flags "something may drop away ahead" to be spoken
    with hedging -- it is not a stair detector and must never be described as
    one to the user.
    """
    height, width = depth.shape[:2]
    if height < 8 or width < columns:
        return []

    band = depth[int(height * (1 - _FLOOR_BAND)) :, :]
    if band.shape[0] < 4:
        return []

    hints: list[DropoffHint] = []
    column_width = width // columns

    for index in range(columns):
        start = index * column_width
        strip = band[:, start : start + column_width]
        if strip.size == 0:
            continue

        # Walking down the strip, the floor should get steadily nearer. A sharp
        # reversal means the surface fell away.
        profile = np.median(strip, axis=1)
        deltas = np.diff(profile)
        worst = float(deltas.min()) if deltas.size else 0.0

        if worst < -_DROPOFF_DELTA:
            hints.append(
                DropoffHint(
                    azimuth_deg=pixel_to_azimuth(
                        start + column_width / 2, width, hfov_deg
                    ),
                    severity=min(1.0, abs(worst) / _DROPOFF_DELTA),
                )
            )

    return hints
