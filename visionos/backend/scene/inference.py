"""Predictions from incomplete evidence.

The tracker confirms an object only after it has been seen twice, and the
hazard engine demands more still. That is right for warnings, but it means a
scan answers only from what is beyond doubt: a blind user asking "what's
here?" gets three objects when the room is obviously a kitchen.

This module reads the same evidence the way a person would:

  what the confirmed objects imply together   a fridge, a sink and a counter
                                              are a kitchen
  what was glimpsed but not yet confirmed     spoken as a guess, never a fact,
                                              and only when the detector is sure
  what left the frame a moment ago            still there, now behind you

Every inference is hedged in its wording, and nothing here feeds the hazard
engine. Speaking a wrong guess as a guess is recoverable; speaking it as a
fact is not.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from backend.perception.geometry import clock_position
from backend.perception.vocabulary import LANDMARK_CLASSES, is_obstacle
from backend.scene.model import SceneModel, SceneObject
from backend.scene.queries import summarize
from backend.speech.phrasing import join_spoken, with_article

# Which objects imply which kind of space, and how strongly. Weights are
# evidence, not probabilities: a bed alone settles "bedroom"; a chair alone
# settles nothing, because chairs are everywhere.
ROOMS: list[tuple[str, dict[str, float]]] = [
    ("kitchen", {"refrigerator": 2.0, "microwave": 1.5, "sink": 1.0, "counter": 1.0,
                 "bowl": 0.5, "cup": 0.3, "bottle": 0.3}),
    ("bathroom", {"toilet": 3.0, "sink": 1.0}),
    ("bedroom", {"bed": 3.0}),
    ("office", {"desk": 1.5, "laptop": 1.5, "keyboard": 1.0, "chair": 0.5, "book": 0.3}),
    ("dining area", {"dining table": 2.0, "table": 1.5, "chair": 0.5, "cup": 0.3, "bowl": 0.3}),
    ("living room", {"couch": 2.0, "tv": 1.5, "potted plant": 0.5, "remote": 0.5, "chair": 0.3}),
    ("corridor", {"hallway": 2.0, "door": 1.0, "doorway": 1.0, "exit sign": 1.5, "elevator": 1.5,
                  "handrail": 1.0, "stairs": 1.0, "staircase": 1.0}),
    ("street", {"bus": 2.0, "car": 1.5, "truck": 1.5, "motorcycle": 1.0, "bicycle": 0.7,
                "pole": 0.7}),
]
# Evidence needed before a room is named. A fridge plus a sink, a bed
# alone or a toilet alone reach it; a door plus a handrail do not. There
# used to be a lower tier spoken as "this may be", but in live use the
# user heard guesses that were plainly wrong, and a wrong guess is not
# made harmless by hedging it. Below this score the room is not named.
_ROOM_MIN_SCORE = 3.0
# A second copy of an object adds evidence; a fifth chair does not.
_MAX_COUNT_PER_LABEL = 2

# A glimpsed object is only mentioned when the detector was at least this
# sure of it. This was 0.25, and in live use the hints were hallucinations:
# an open-vocabulary detector at a quarter confidence sees doors in
# shadows. The user asked for predictions only above 80%, and a hint the
# user cannot check has to clear that bar or stay unspoken.
_TENTATIVE_MIN_CONFIDENCE = 0.8
# A landmark that left the frame is worth a reminder for this long.
_REMINDER_MAX_UNSEEN_S = 15.0
_REMINDER_MIN_UNSEEN_S = 1.0


@dataclass(slots=True)
class Tentative:
    """Something the detector saw in the latest frame that is not yet confirmed."""

    label: str
    azimuth_deg: float
    distance_m: float | None
    confidence: float

    @property
    def clock(self) -> str:
        return clock_position(self.azimuth_deg)


def room_guess(labels: Iterable[str]) -> tuple[str, float] | None:
    """The kind of space these objects add up to, with its evidence score."""
    counts = Counter(labels)
    best: tuple[str, float] | None = None
    for room, weights in ROOMS:
        score = sum(
            weights.get(label, 0.0) * min(count, _MAX_COUNT_PER_LABEL)
            for label, count in counts.items()
        )
        if score >= _ROOM_MIN_SCORE and (best is None or score > best[1]):
            best = (room, score)
    return best


def room_sentence(labels: Iterable[str]) -> str | None:
    """Named only on decisive evidence, and then plainly; there is no
    hedged tier, because the evidence is either enough or it is not."""
    guess = room_guess(labels)
    if guess is None:
        return None
    room, _score = guess
    return f"This looks like {with_article(room)}."


def tentative_objects(detections, scene: SceneModel, limit: int = 2) -> list[Tentative]:
    """Latest-frame detections the scene model has not confirmed, best first.

    Navigation-relevant things (doors, stairs, obstacles) outrank tabletop
    objects: a possible doorway is worth a hedged mention, a possible cup is
    not.
    """
    confirmed = {o.label for o in scene.all_objects() if o.visible}
    seen: set[str] = set()
    out: list[Tentative] = []
    for detection in sorted(detections, key=lambda d: -d.confidence):
        if detection.confidence < _TENTATIVE_MIN_CONFIDENCE:
            continue
        if detection.label in confirmed or detection.label in seen:
            continue
        seen.add(detection.label)
        out.append(
            Tentative(
                label=detection.label,
                azimuth_deg=detection.azimuth_deg,
                distance_m=detection.distance_m,
                confidence=detection.confidence,
            )
        )
    out.sort(
        key=lambda t: (
            not (t.label in LANDMARK_CLASSES or is_obstacle(t.label)),
            -t.confidence,
        )
    )
    return out[:limit]


def _place(clock: str, distance_m: float | None) -> str:
    if distance_m is None:
        return f"at your {clock}"
    return f"at your {clock}, about {distance_m:.0f} meter{'s' if round(distance_m) != 1 else ''}"


def tentative_sentence(tentative: list[Tentative], anything_confirmed: bool) -> str | None:
    if not tentative:
        return None
    parts = [f"{with_article(t.label)} {_place(t.clock, t.distance_m)}" for t in tentative]
    lead = "I think there may also be" if anything_confirmed else "I can't confirm anything yet, but I think there may be"
    return f"{lead} {join_spoken(parts)}."


def remembered_landmark(scene: SceneModel) -> SceneObject | None:
    """The navigation landmark that most recently left the frame, if any."""
    candidates = [
        o
        for o in scene.all_objects()
        if not o.visible
        and o.label in LANDMARK_CLASSES
        and _REMINDER_MIN_UNSEEN_S <= o.unseen_s <= _REMINDER_MAX_UNSEEN_S
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o: o.unseen_s)


def reminder_sentence(scene: SceneModel) -> str | None:
    landmark = remembered_landmark(scene)
    if landmark is None:
        return None
    # Past tense on purpose: the position is where it was when last seen,
    # and the user may have turned since.
    return f"The {landmark.label} was at your {landmark.clock} a moment ago."


def describe_scene(scene: SceneModel, detections=()) -> str:
    """A scan answer that reasons past the confirmed list.

    Room first, so the user gets the gist before the inventory; then what is
    confirmed; then what is only glimpsed, hedged; then a landmark that just
    left view. Two to four short sentences.
    """
    visible_labels = [o.label for o in scene.all_objects() if o.visible]
    tentative = tentative_objects(detections, scene)

    sentences: list[str] = []
    # The room comes from confirmed objects only. Naming it from glimpses
    # stacked a guess on a guess, and that is where the wrong rooms came
    # from.
    room = room_sentence(visible_labels)
    if room:
        sentences.append(room)

    summary = summarize(scene)
    if visible_labels:
        sentences.append(summary)

    hint = tentative_sentence(tentative, anything_confirmed=bool(visible_labels))
    if hint:
        sentences.append(hint)
    elif not visible_labels:
        sentences.append(summary)

    reminder = reminder_sentence(scene)
    if reminder:
        sentences.append(reminder)

    return " ".join(sentences)


def snapshot_extras(scene: SceneModel, detections=()) -> dict:
    """Inferences for the LLM context and the dashboard."""
    visible_labels = [o.label for o in scene.all_objects() if o.visible]
    guess = room_guess(visible_labels)
    return {
        "room": guess[0] if guess else None,
        "tentative": [
            {
                "label": t.label,
                "clock": t.clock,
                "distance_m": round(t.distance_m, 1) if t.distance_m is not None else None,
                "confidence": round(t.confidence, 2),
            }
            for t in tentative_objects(detections, scene)
        ],
    }
