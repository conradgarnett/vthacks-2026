"""Is the wearer eating, and what does the food look like?

The scan already infers "eating" for a person in the picture with a fork or
a bowl in reach. Here the person is the wearer, so the picture is the
first-person view: what is being eaten sits low in the frame and near the
camera, and a utensil, a cup or a plate keeps appearing with it, or the food
itself fills enough of the view to be in the hand.

One frame proves nothing (open-vocabulary detection flickers), so everything
is counted over a window of recent frames, and a food counts only above the
user's 80% rule in several of them. What comes out is a trigger, not
evidence: it says a label may be worth reading and, for the few foods that
are the allergen itself, earns a hedged warning. The allergen decision is
made from text, never from here.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

from backend.alerts.allergy import is_food

# Things that keep appearing while someone eats. The floor is the
# detector's own, a step up: they are a cue, not a claim.
EATING_CUES: frozenset[str] = frozenset({
    "fork", "spoon", "knife", "cup", "bowl", "plate", "drinking glass", "hand",
})
WINDOW_S = 6.0
MIN_FRAMES = 3
CUE_MIN_CONFIDENCE = 0.5
FOOD_MIN_CONFIDENCE = 0.8
# Where a thing being eaten sits in a first-person view: its centre in the
# lower part of the frame (0 is the top) and its box a fair fraction of the
# frame tall. A pizza across the room is high and small; one on the plate in
# front of the wearer is low and large.
LOW_FROM = 0.40
NEAR_HEIGHT = 0.10
# A food this tall in view is in the hand, which is a cue in itself: a
# sandwich needs no fork.
IN_HAND_HEIGHT = 0.25


@dataclass(frozen=True)
class Sighting:
    label: str
    confidence: float
    y_center: float  # fraction of the frame height, 0 at the top
    height: float  # fraction of the frame height

    @property
    def low_and_near(self) -> bool:
        return self.y_center >= LOW_FROM and self.height >= NEAR_HEIGHT


@dataclass(frozen=True)
class FoodSeen:
    label: str
    frames: int  # frames in the window it was sure and low and near in
    confidence: float  # the lowest of those frames: a claim is as sure as its weakest frame
    in_hand: bool  # tall enough in view to be held, in enough frames


@dataclass(frozen=True)
class Eating:
    foods: tuple[FoodSeen, ...]
    cue_frames: int
    window_frames: int
    cued: bool  # a utensil, a cup or a plate kept appearing low in view

    @property
    def in_hand(self) -> bool:
        return any(food.in_hand for food in self.foods)

    @property
    def confirmed(self) -> bool:
        """Food in front of the wearer, and a reason to think it is being eaten."""
        return bool(self.foods) and (self.cued or self.in_hand)


def sightings_from(detections: Iterable, frame_size: tuple[int, int] | None) -> list[Sighting]:
    """Detections (with a pixel box) as frame-relative sightings."""
    if not frame_size:
        return []
    width, height = frame_size
    if not width or not height:
        return []
    out: list[Sighting] = []
    for d in detections:
        box = d.box
        out.append(Sighting(
            label=d.label,
            confidence=float(d.confidence),
            y_center=((box.y1 + box.y2) / 2.0) / height,
            height=(box.y2 - box.y1) / height,
        ))
    return out


class EatingDetector:
    def __init__(
        self,
        *,
        window_s: float = WINDOW_S,
        min_frames: int = MIN_FRAMES,
        food_min_confidence: float = FOOD_MIN_CONFIDENCE,
        cue_min_confidence: float = CUE_MIN_CONFIDENCE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window_s = window_s
        self.min_frames = min_frames
        self.food_min_confidence = food_min_confidence
        self.cue_min_confidence = cue_min_confidence
        self._clock = clock
        # (time, sightings) per processed frame; the window is by time, the
        # cap keeps a fast machine's history bounded.
        self._frames: deque[tuple[float, list[Sighting]]] = deque(maxlen=64)

    def observe(self, detections: Iterable, frame_size: tuple[int, int] | None, now: float | None = None) -> None:
        self.observe_sightings(sightings_from(detections, frame_size), now)

    def observe_sightings(self, sightings: list[Sighting], now: float | None = None) -> None:
        now = self._clock() if now is None else now
        self._frames.append((now, list(sightings)))

    def recent(self, now: float | None = None) -> list[list[Sighting]]:
        now = self._clock() if now is None else now
        return [s for at, s in self._frames if now - at <= self.window_s]

    def eating(self, now: float | None = None) -> Eating:
        frames = self.recent(now)
        cue_frames = sum(
            1 for frame in frames
            if any(s.label in EATING_CUES and s.confidence >= self.cue_min_confidence and s.y_center >= LOW_FROM
                   for s in frame)
        )
        counts: dict[str, list[float]] = {}
        tall: dict[str, int] = {}
        for frame in frames:
            seen_here: dict[str, Sighting] = {}
            for s in frame:
                if not is_food(s.label) or s.confidence < self.food_min_confidence or not s.low_and_near:
                    continue
                # The surest box per label per frame; a frame counts once.
                if s.label not in seen_here or s.confidence > seen_here[s.label].confidence:
                    seen_here[s.label] = s
            for label, s in seen_here.items():
                counts.setdefault(label, []).append(s.confidence)
                if s.height >= IN_HAND_HEIGHT:
                    tall[label] = tall.get(label, 0) + 1
        foods = tuple(
            FoodSeen(label, len(confidences), min(confidences), tall.get(label, 0) >= self.min_frames)
            for label, confidences in sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            if len(confidences) >= self.min_frames
        )
        return Eating(foods, cue_frames, len(frames), cue_frames >= self.min_frames)

    def reset(self) -> None:
        self._frames.clear()
