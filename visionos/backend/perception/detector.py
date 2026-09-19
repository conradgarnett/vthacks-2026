"""YOLO object detection with size-based metric distance.

Why distance comes from object size, not the depth network: monocular depth
models output *relative* inverse depth with no metric scale. Calibrating that
to meters needs a reference, and guessing the reference is how you end up
confidently telling someone a wall is two meters away when it's five.

Apparent size gives real metric distance directly via the pinhole model:

    distance = (real_height_m * focal_px) / pixel_height

It only works for classes whose real height we know, which is exactly the set
of common obstacles that matter for safety. The depth network (depth.py) then
supplies relative ordering and covers everything the detector has no prior for.

COCO has no "door" class. That gap is deliberate architecture, not an
oversight: the local detector owns the fast safety-critical path, and Claude
handles open-vocabulary queries ("where's the door", "read that sign").
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from functools import partial

from backend.config import Settings
from backend.perception.geometry import (
    BoundingBox,
    clock_position,
    pixel_to_azimuth,
    pixel_to_elevation,
    vertical_fov_deg,
)

log = logging.getLogger(__name__)

# Typical real-world heights in meters. Deliberately conservative: a distance
# that reads slightly too close is a safe error, too far is not.
CLASS_HEIGHTS_M: dict[str, float] = {
    "person": 1.70,
    "chair": 0.90,
    "couch": 0.80,
    "bed": 0.60,
    "dining table": 0.75,
    "tv": 0.60,
    "laptop": 0.25,
    "backpack": 0.45,
    "handbag": 0.30,
    "suitcase": 0.65,
    "bottle": 0.25,
    "cup": 0.12,
    "bowl": 0.08,
    "potted plant": 0.50,
    "refrigerator": 1.70,
    "oven": 0.85,
    "microwave": 0.30,
    "sink": 0.25,
    "toilet": 0.75,
    "book": 0.24,
    "clock": 0.30,
    "vase": 0.30,
    "bicycle": 1.05,
    "motorcycle": 1.10,
    "car": 1.50,
    "bus": 3.00,
    "truck": 3.20,
    "traffic light": 0.90,
    "stop sign": 0.75,
    "bench": 0.85,
    "dog": 0.55,
    "cat": 0.30,
    "tv monitor": 0.55,
    "keyboard": 0.03,
    "cell phone": 0.15,
}

# Objects a user can walk into. Drives hazard filtering.
OBSTACLE_CLASSES = frozenset(
    {
        "person", "chair", "couch", "bed", "dining table", "bicycle",
        "motorcycle", "car", "bus", "truck", "bench", "potted plant",
        "refrigerator", "oven", "toilet", "sink", "suitcase", "dog",
    }
)

_MIN_DISTANCE_M = 0.3
_MAX_DISTANCE_M = 30.0


@dataclass(slots=True)
class Detection:
    label: str
    confidence: float
    box: BoundingBox
    azimuth_deg: float
    elevation_deg: float
    distance_m: float | None
    distance_is_estimated: bool

    @property
    def clock(self) -> str:
        return clock_position(self.azimuth_deg)

    @property
    def is_obstacle(self) -> bool:
        return self.label in OBSTACLE_CLASSES


def distance_from_height(
    label: str, pixel_height: float, frame_height: int, vfov_deg: float
) -> float | None:
    """Metric distance from apparent size. None when we have no prior."""
    real_height = CLASS_HEIGHTS_M.get(label)
    if real_height is None or pixel_height <= 1 or frame_height <= 0:
        return None

    focal_px = (frame_height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    distance = (real_height * focal_px) / pixel_height
    return round(min(max(distance, _MIN_DISTANCE_M), _MAX_DISTANCE_M), 2)


class Detector:
    """Thread-pooled YOLO. Never call the model on the event loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = None
        self._device = self._resolve_device(settings.perception_device)

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
        from ultralytics import YOLO

        self._model = YOLO(self._settings.detector_weights)
        log.info(
            "Detector loaded: %s on %s", self._settings.detector_weights, self._device
        )

    def detect_sync(self, frame_bgr) -> list[Detection]:
        if self._model is None:
            self.load()

        height, width = frame_bgr.shape[:2]
        vfov = vertical_fov_deg(self._settings.camera_hfov_deg, width, height)

        results = self._model.predict(
            frame_bgr,
            conf=self._settings.detector_confidence,
            device=self._device,
            verbose=False,
        )

        detections: list[Detection] = []
        for result in results:
            names = result.names
            for raw in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in raw.xyxy[0].tolist())
                box = BoundingBox(x1, y1, x2, y2)
                label = names[int(raw.cls[0])]
                center_x, center_y = box.center
                distance = distance_from_height(label, box.height, height, vfov)

                detections.append(
                    Detection(
                        label=label,
                        confidence=float(raw.conf[0]),
                        box=box,
                        azimuth_deg=pixel_to_azimuth(
                            center_x, width, self._settings.camera_hfov_deg
                        ),
                        elevation_deg=pixel_to_elevation(center_y, height, vfov),
                        distance_m=distance,
                        distance_is_estimated=distance is not None,
                    )
                )
        return detections

    async def detect(self, frame_bgr) -> list[Detection]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self.detect_sync, frame_bgr))
