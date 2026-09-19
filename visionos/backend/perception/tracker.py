"""Frame-to-frame identity via IoU association.

Stable IDs are what make object permanence possible: without them the scene
model cannot tell "the same chair, still there" from "a new chair", and every
answer would reset each frame.

Greedy IoU matching, not a Kalman filter. At 30+ fps, frame-to-frame motion is
small enough that greedy matching is accurate, and the failure mode (a swapped
ID between two adjacent identical chairs) costs nothing a user would notice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from itertools import count

from backend.perception.detector import Detection

_ids = count(1)


@dataclass(slots=True)
class Track:
    track_id: int
    detection: Detection
    first_seen: float
    last_seen: float
    hit_count: int = 1
    # Consecutive frames this track has gone unmatched.
    misses: int = 0

    @property
    def label(self) -> str:
        return self.detection.label

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.first_seen

    @property
    def unseen_s(self) -> float:
        return time.monotonic() - self.last_seen


@dataclass
class Tracker:
    """Greedy IoU tracker with a confirmation threshold."""

    iou_threshold: float = 0.3
    # A track must be seen this many times before it is reported. Suppresses
    # single-frame false positives, which would otherwise be spoken aloud.
    min_hits: int = 2
    # How long an unmatched track survives, enabling "it was there a moment ago".
    max_unseen_s: float = 20.0

    tracks: dict[int, Track] = field(default_factory=dict)

    def update(self, detections: list[Detection]) -> list[Track]:
        """Associate detections to tracks. Returns confirmed, currently-visible tracks."""
        now = time.monotonic()
        unmatched = set(self.tracks)

        # Best-first so a strong match is never stolen by a weaker one.
        candidates = [
            (track.detection.box.iou(det.box), tid, index)
            for tid, track in self.tracks.items()
            for index, det in enumerate(detections)
            if track.label == det.label
        ]
        candidates.sort(reverse=True)

        claimed_detections: set[int] = set()
        for iou, tid, index in candidates:
            if iou < self.iou_threshold:
                break
            if tid not in unmatched or index in claimed_detections:
                continue

            track = self.tracks[tid]
            track.detection = detections[index]
            track.last_seen = now
            track.hit_count += 1
            track.misses = 0
            unmatched.discard(tid)
            claimed_detections.add(index)

        for tid in unmatched:
            self.tracks[tid].misses += 1

        for index, detection in enumerate(detections):
            if index not in claimed_detections:
                track_id = next(_ids)
                self.tracks[track_id] = Track(
                    track_id=track_id,
                    detection=detection,
                    first_seen=now,
                    last_seen=now,
                )

        self._evict(now)
        return self.visible()

    def _evict(self, now: float) -> None:
        stale = [
            tid
            for tid, track in self.tracks.items()
            if now - track.last_seen > self.max_unseen_s
        ]
        for tid in stale:
            del self.tracks[tid]

    def visible(self) -> list[Track]:
        """Confirmed tracks matched in the most recent frame."""
        return [
            t for t in self.tracks.values() if t.misses == 0 and t.hit_count >= self.min_hits
        ]

    def remembered(self) -> list[Track]:
        """Confirmed tracks that have left view but are still within memory."""
        return [
            t for t in self.tracks.values() if t.misses > 0 and t.hit_count >= self.min_hits
        ]

    def reset(self) -> None:
        self.tracks.clear()
