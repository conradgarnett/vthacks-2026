"""The egocentric scene model.

This is the core of the product. Existing tools answer "what is in this
photo?"; this answers "where is everything, relative to me, right now" -- and
keeps answering after an object leaves the frame.

Two properties matter:

  Object permanence. A departed object is retained, flagged not-visible, with
  a confidence that decays over time. That is what lets the assistant say "the
  door was at your two o'clock a moment ago" instead of "I don't see a door".

  Groundedness. snapshot() is injected into Claude's context so answers cite
  tracked geometry rather than guessing from pixels.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backend.perception.geometry import clock_position, describe_direction, region_of
from backend.perception.tracker import Track


@dataclass(slots=True)
class SceneObject:
    object_id: int
    label: str
    azimuth_deg: float
    distance_m: float | None
    confidence: float
    first_seen: float
    last_seen: float
    visible: bool
    is_obstacle: bool
    # Frames this object has actually been detected in. Open-vocabulary
    # detection flickers, and a one-frame ghost is indistinguishable from a
    # real object on any single frame -- persistence is the only signal that
    # separates them.
    hit_count: int = 1

    @property
    def clock(self) -> str:
        return clock_position(self.azimuth_deg)

    @property
    def direction(self) -> str:
        return describe_direction(self.azimuth_deg)

    @property
    def region(self) -> str:
        return region_of(self.azimuth_deg)

    @property
    def unseen_s(self) -> float:
        return max(0.0, time.monotonic() - self.last_seen)

    def decayed_confidence(self, memory_s: float) -> float:
        """Confidence falls linearly while out of view.

        An assistive tool must not sound equally certain about something it
        saw 15 seconds ago and something it can see now.
        """
        if self.visible:
            return self.confidence
        remaining = max(0.0, 1.0 - self.unseen_s / memory_s)
        return round(self.confidence * remaining, 3)

    def to_dict(self, memory_s: float) -> dict[str, Any]:
        return {
            "id": self.object_id,
            "label": self.label,
            "clock": self.clock,
            "direction": self.direction,
            "region": self.region,
            "azimuth_deg": round(self.azimuth_deg, 1),
            "distance_m": round(self.distance_m, 2) if self.distance_m else None,
            "confidence": self.decayed_confidence(memory_s),
            "visible": self.visible,
            "unseen_s": round(self.unseen_s, 1),
        }


@dataclass
class SceneModel:
    """Persistent egocentric state, rebuilt from tracks each frame."""

    memory_s: float = 20.0
    objects: dict[int, SceneObject] = field(default_factory=dict)

    def update(self, visible: list[Track], remembered: list[Track]) -> None:
        seen_ids: set[int] = set()

        for track, is_visible in [(t, True) for t in visible] + [
            (t, False) for t in remembered
        ]:
            seen_ids.add(track.track_id)
            detection = track.detection
            existing = self.objects.get(track.track_id)

            if existing is None:
                self.objects[track.track_id] = SceneObject(
                    object_id=track.track_id,
                    label=track.label,
                    azimuth_deg=detection.azimuth_deg,
                    distance_m=detection.distance_m,
                    confidence=detection.confidence,
                    first_seen=track.first_seen,
                    last_seen=track.last_seen,
                    visible=is_visible,
                    is_obstacle=detection.is_obstacle,
                    hit_count=track.hit_count,
                )
                continue

            existing.visible = is_visible
            existing.last_seen = track.last_seen
            existing.hit_count = track.hit_count
            if is_visible:
                # Only refresh geometry from a live observation; a remembered
                # object must keep the position it was last actually seen at.
                existing.azimuth_deg = detection.azimuth_deg
                existing.distance_m = detection.distance_m
                existing.confidence = detection.confidence

        for object_id in set(self.objects) - seen_ids:
            del self.objects[object_id]

    def all_objects(self) -> list[SceneObject]:
        """Nearest first; objects with no distance estimate sort last."""
        return sorted(
            self.objects.values(),
            key=lambda o: (o.distance_m is None, o.distance_m or 0.0),
        )

    def changes_since(self, seconds: float) -> list[SceneObject]:
        """Objects that appeared within the window. Powers 'what changed?'."""
        cutoff = time.monotonic() - seconds
        return [o for o in self.all_objects() if o.first_seen >= cutoff]

    def snapshot(self, limit: int = 12) -> dict[str, Any]:
        """Compact state for LLM context and the judge dashboard.

        Capped: this rides along with every question, so an unbounded list
        would quietly inflate the cost and latency of every single answer.
        """
        objects = self.all_objects()[:limit]
        return {
            "sense": "sight",
            "object_count": len(self.objects),
            "objects": [o.to_dict(self.memory_s) for o in objects],
        }
