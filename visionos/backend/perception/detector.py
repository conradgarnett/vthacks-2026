"""Object detection with size-based metric distance.

Two modes. "open" runs YOLO-World against the curated vocabulary in
vocabulary.py, which is what makes doors, stairs and handrails detectable at
all -- COCO has none of them. "coco" runs a standard YOLO checkpoint, kept as
a fallback for when open-vocabulary output is too noisy for a given room.

Measured on this machine: YOLO-World 25 ms, yolo11n 13 ms, yolo11m 43 ms. Open
vocabulary is both broader and cheaper than the medium closed-set model, so it
is the default.

Why distance comes from object size, not the depth network: monocular depth is
*relative* and unscaled. Calibrating it to meters needs a reference, and a
wrong reference means confidently telling someone a wall is two meters away
when it is five. Apparent size gives metric distance directly:

    distance = (real_height_m * focal_px) / pixel_height
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from backend.config import Settings
from backend.perception.runtime import run_inference
from backend.perception.geometry import (
    BoundingBox,
    pixel_to_azimuth,
    pixel_to_elevation,
    vertical_fov_deg,
)
from backend.perception.vocabulary import (
    CLASS_HEIGHTS_M,
    CLASS_NAMES,
    OBSTACLE_CLASSES,
    confidence_floor,
    height_for,
    is_obstacle,
)

log = logging.getLogger(__name__)

__all__ = [
    "CLASS_HEIGHTS_M",
    "OBSTACLE_CLASSES",
    "Detection",
    "Detector",
    "distance_from_height",
]

_MIN_DISTANCE_M = 0.3
_MAX_DISTANCE_M = 30.0


@dataclass(slots=True)
class Detection:
    label: str
    confidence: float
    box: BoundingBox
    azimuth_deg: float
    elevation_deg: float
    # None when the vocabulary has no height prior for this class: direction
    # is still reported, distance is not.
    distance_m: float | None

    @property
    def is_obstacle(self) -> bool:
        return is_obstacle(self.label)


def distance_from_height(
    label: str, pixel_height: float, frame_height: int, vfov_deg: float
) -> float | None:
    """Metric distance from apparent size. None when we have no prior."""
    real_height = height_for(label)
    if real_height is None or pixel_height <= 1 or frame_height <= 0:
        return None

    focal_px = (frame_height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    distance = (real_height * focal_px) / pixel_height
    return round(min(max(distance, _MIN_DISTANCE_M), _MAX_DISTANCE_M), 2)


class Detector:
    """Thread-pooled detector. Never call the model on the event loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = None
        self._device = self._resolve_device(settings.perception_device)
        self._open_vocab = settings.detector_mode == "open"

    @staticmethod
    def _resolve_device(preference: str) -> str:
        if preference != "auto":
            return preference
        try:
            import torch

            if torch.backends.mps.is_available():
                return "mps"
            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"

    def load(self) -> None:
        """Blocking. Call once at startup, never mid-demo."""
        if self._model is not None:
            return

        if self._open_vocab:
            from ultralytics import YOLOWorld

            self._model = YOLOWorld(self._settings.open_vocab_weights)
            # Embedding the class list is the expensive part of open-vocabulary
            # detection, so it happens once here rather than per frame.
            self._model.set_classes(CLASS_NAMES)
            log.info(
                "Detector: %s on %s, %d open-vocabulary classes",
                self._settings.open_vocab_weights,
                self._device,
                len(CLASS_NAMES),
            )
        else:
            from ultralytics import YOLO

            self._model = YOLO(self._settings.detector_weights)
            log.info(
                "Detector: %s on %s (COCO)", self._settings.detector_weights, self._device
            )

    def detect_sync(self, frame_bgr) -> list[Detection]:
        if self._model is None:
            self.load()

        height, width = frame_bgr.shape[:2]
        vfov = vertical_fov_deg(self._settings.camera_hfov_deg, width, height)
        base_confidence = self._settings.detector_confidence

        results = self._model.predict(
            frame_bgr,
            conf=base_confidence,
            device=self._device,
            verbose=False,
        )

        detections: list[Detection] = []
        for result in results:
            names = result.names
            for raw in result.boxes:
                label = names[int(raw.cls[0])]
                confidence = float(raw.conf[0])

                # A phantom staircase stops someone dead; a phantom door sends
                # them into a wall. Those classes clear a higher bar.
                if confidence < confidence_floor(label, base_confidence):
                    continue

                x1, y1, x2, y2 = (float(v) for v in raw.xyxy[0].tolist())
                box = BoundingBox(x1, y1, x2, y2)
                center_x, center_y = box.center
                distance = distance_from_height(label, box.height, height, vfov)

                detections.append(
                    Detection(
                        label=label,
                        confidence=confidence,
                        box=box,
                        azimuth_deg=pixel_to_azimuth(
                            center_x, width, self._settings.camera_hfov_deg
                        ),
                        elevation_deg=pixel_to_elevation(center_y, height, vfov),
                        distance_m=distance,
                    )
                )
        return detections

    async def detect(self, frame_bgr) -> list[Detection]:
        # Shared single-thread pool: MPS is not thread-safe and concurrent
        # access crashes the process. See perception/runtime.py.
        return await run_inference(self.detect_sync, frame_bgr)
