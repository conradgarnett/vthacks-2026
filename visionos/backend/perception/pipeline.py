"""Per-frame perception, with detection and depth on separate cadences.

Measured on this machine: detection 9 ms, depth 117 ms. Running both per frame
would put the safety path 13x over budget, so they are decoupled --

  detection  every frame, feeds the tracker and the hazard engine
  depth      every Nth frame, feeds drop-off hints only

The consequence that matters: an obstacle alert never waits on the slow model.
"""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from backend.config import Settings
from backend.perception.depth import DepthEstimator, DropoffHint, find_dropoffs
from backend.perception.detector import Detector
from backend.perception.tracker import Tracker
from backend.scene.model import SceneModel
from backend.telemetry import LatencyTrace

log = logging.getLogger(__name__)

# Depth costs ~117 ms; at one in six frames it stays well clear of detection.
DEPTH_EVERY_N_FRAMES = 6


class PerceptionPipeline:
    """Owns the models and the per-connection scene state."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.detector = Detector(settings)
        self.depth = DepthEstimator(settings)
        self.tracker = Tracker(
            iou_threshold=settings.track_iou_threshold,
            max_unseen_s=settings.object_memory_s,
        )
        self.scene = SceneModel(memory_s=settings.object_memory_s)

        self.dropoffs: list[DropoffHint] = []
        # Every detection from the latest frame, confirmed or not. The scene
        # model only admits what the tracker has seen twice; inference reads
        # the rest as hints, spoken as guesses.
        self.last_detections: list = []
        self.last_trace: dict = {}
        self.dropped_frames = 0
        self._frame_index = 0
        self._depth_task: asyncio.Task | None = None
        self._enabled = True
        self._busy = False

    def warmup(self) -> None:
        """Load weights up front. Never pay this cost mid-demo."""
        try:
            self.detector.load()
        except Exception:
            log.exception("Detector failed to load; perception disabled")
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def device(self) -> str:
        """Where the detector runs: cpu, cuda or mps."""
        return getattr(self.detector, "_device", "cpu")

    async def process(self, frame_jpeg: bytes) -> None:
        """Decode, detect, track, integrate. Safe to call at frame rate.

        Drops frames rather than queueing them. Inference is serialized on one
        thread, so a phone sending faster than the GPU drains would otherwise
        build an unbounded backlog and every answer would describe a room the
        user has already walked out of. A stale frame is worth less than none.
        """
        if not self._enabled or self._busy:
            self.dropped_frames += 1
            return

        self._busy = True
        try:
            trace = LatencyTrace(label="perception")
            loop = asyncio.get_running_loop()

            # Decode is pure CPU, so it stays off the inference thread.
            with trace.stage("decode"):
                frame = await loop.run_in_executor(None, _decode_jpeg, frame_jpeg)
            if frame is None:
                return

            with trace.stage("detect"):
                detections = await self.detector.detect(frame)
            self.last_detections = detections

            with trace.stage("track"):
                visible = self.tracker.update(detections)
                self.scene.update(visible, self.tracker.remembered())

            self._frame_index += 1
            if self._frame_index % DEPTH_EVERY_N_FRAMES == 0:
                self._maybe_run_depth(frame)

            trace.mark("total")
            self.last_trace = trace.to_dict()
        finally:
            self._busy = False

    def _maybe_run_depth(self, frame: np.ndarray) -> None:
        """Fire-and-forget: depth must never stall the detection loop."""
        if self._depth_task and not self._depth_task.done():
            return
        self._depth_task = asyncio.create_task(self._run_depth(frame))

    async def _run_depth(self, frame: np.ndarray) -> None:
        try:
            from PIL import Image

            image = Image.fromarray(frame[:, :, ::-1])  # BGR -> RGB
            depth_map = await self.depth.estimate(image)
            self.dropoffs = find_dropoffs(depth_map, self._settings.camera_hfov_deg)
        except Exception:
            log.exception("Depth pass failed; drop-off hints unavailable")
            self.dropoffs = []

    def snapshot(self) -> dict:
        from backend.scene.inference import snapshot_extras

        state = self.scene.snapshot()
        state.update(snapshot_extras(self.scene, self.last_detections))
        state["dropoff_hints"] = [
            {"azimuth_deg": round(h.azimuth_deg, 1), "severity": round(h.severity, 2)}
            for h in self.dropoffs
        ]
        state["perception_ms"] = self.last_trace.get("stages", {})
        state["updated_at"] = round(time.monotonic(), 2)
        return state


def _decode_jpeg(payload: bytes) -> np.ndarray | None:
    import cv2

    buffer = np.frombuffer(payload, dtype=np.uint8)
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)
