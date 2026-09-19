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
from dataclasses import dataclass, replace
from typing import Iterable

from backend.perception.geometry import clock_position, describe_direction
from backend.perception.vocabulary import LANDMARK_CLASSES, is_obstacle
from backend.scene.model import SceneModel, SceneObject
from backend.scene.queries import summarize
from backend.speech.phrasing import join_spoken, pluralize, with_article

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


# --- Scan pictures ---------------------------------------------------------
#
# A scan used to be an inventory: the nearest few objects, each with a clock
# position and a distance, nothing about how they sit together. Looking down
# a hallway at a person sitting at a table with two chairs, it said "a person
# about 6 meters away". The picture below says where you are, then groups
# what belongs together and says what the group is doing.


@dataclass(slots=True)
class Seen:
    """One thing a scan saw, with the evidence for it.

    `frames` is how many of the scan's frames had it: two frames agreeing is
    what makes a sighting count, the same evidence the reader uses for text.
    `box` is normalized (x1, y1, x2, y2) within the frame, for relationships.
    """

    label: str
    confidence: float
    azimuth_deg: float
    distance_m: float | None
    frames: int = 2
    box: tuple[float, float, float, float] | None = None


# Things that describe the place rather than sit in it.
_SETTING_LABELS = {"hallway": "You're looking down a hallway."}
# Furniture a person sits at or in.
_TABLES = {"table", "dining table", "desk", "counter"}
_SEATS = {"chair", "couch", "bench"}
# Two things within this angle and distance of each other belong to one group.
_GROUP_AZIMUTH_DEG = 20.0
_GROUP_DISTANCE_RATIO = 1.6
_GROUP_DISTANCE_M = 1.5
# A person's box overlapping a seat's by this much means they are in it.
_SITTING_OVERLAP = 0.05


def _about(distance_m: float | None) -> str:
    if distance_m is None:
        return ""
    if distance_m < 1.0:
        return "less than a meter"
    if distance_m < 3.0:
        return f"about {round(distance_m * 2) / 2:g} meters"
    return f"about {round(distance_m)} meters"


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _together(a: Seen, b: Seen) -> bool:
    if abs(a.azimuth_deg - b.azimuth_deg) > _GROUP_AZIMUTH_DEG:
        return False
    if a.distance_m is None or b.distance_m is None:
        return abs(a.azimuth_deg - b.azimuth_deg) <= _GROUP_AZIMUTH_DEG / 2
    near, far = sorted((a.distance_m, b.distance_m))
    return far - near <= _GROUP_DISTANCE_M or far / max(near, 0.1) <= _GROUP_DISTANCE_RATIO


def group_seen(seen: list[Seen]) -> list[list[Seen]]:
    """Greedy grouping, nearest first, so each thing joins the closest group."""
    ordered = sorted(seen, key=lambda s: (s.distance_m is None, s.distance_m or 0.0, abs(s.azimuth_deg)))
    groups: list[list[Seen]] = []
    for item in ordered:
        for group in groups:
            if _together(group[0], item):
                group.append(item)
                break
        else:
            groups.append([item])
    return groups


def _is_sitting(person: Seen, seats: list[Seen]) -> bool:
    if person.box is None:
        return False
    return any(seat.box is not None and _overlap(person.box, seat.box) >= _SITTING_OVERLAP for seat in seats)


def _counted(items: list[Seen]) -> str:
    counts = Counter(s.label for s in items)
    parts = [with_article(label) if n == 1 else pluralize(n, label) for label, n in counts.items()]
    return join_spoken(parts)


def describe_group(group: list[Seen]) -> str:
    """What a group is, as a person would say it: "a person sitting at a table
    with two chairs, one of them empty"."""
    people = [s for s in group if s.label == "person"]
    seats = [s for s in group if s.label in _SEATS]
    tables = [s for s in group if s.label in _TABLES]
    rest = [s for s in group if s not in people and s not in seats and s not in tables]

    if not people:
        return _counted(group)

    subject = "a person" if len(people) == 1 else pluralize(len(people), "person")
    sitting = any(_is_sitting(p, seats) for p in people)
    verb = "sitting" if sitting else "standing"
    phrase = subject
    if tables and seats:
        phrase += f" {verb} at {with_article(tables[0].label)} with {pluralize(len(seats), seats[0].label) if len(seats) > 1 else with_article(seats[0].label)}"
    elif tables:
        phrase += f" {verb} at {with_article(tables[0].label)}"
    elif seats:
        seat_phrase = with_article(seats[0].label) if len(seats) == 1 else pluralize(len(seats), seats[0].label)
        phrase += f" {verb} {'in' if sitting else 'by'} {seat_phrase}"
    # Seats nobody is in: all of them when the people are standing.
    empty = len(seats) - (len(people) if sitting else 0)
    if seats and empty > 0:
        if len(seats) == 1:
            phrase += ", which is empty"
        else:
            phrase += ", one of them empty" if empty == 1 else f", {empty} of them empty"
    if rest:
        phrase += f", and {_counted(rest)}"
    return phrase


def _where(group: list[Seen], hallway: bool, farthest: bool) -> str:
    distances = [s.distance_m for s in group if s.distance_m is not None]
    azimuth = sum(s.azimuth_deg for s in group) / len(group)
    direction = describe_direction(azimuth)
    where = _about(min(distances)) if distances else ""
    if hallway and farthest and direction == "straight ahead":
        return f"{where} ahead, at the end of the hallway" if where else "at the end of the hallway"
    return f"{where} {direction}".strip()


def describe_scan(scene: SceneModel, seen: list[Seen], glimpsed: Iterable[Seen] = ()) -> str:
    """A scan answer that paints the scene from what two frames agreed on.

    The place first, then each group of things that sit together, nearest
    first, with what the group is doing and where it is; then what was only
    glimpsed, hedged, and only when the detector was sure of it.
    """
    labels = [s.label for s in seen]
    sentences: list[str] = []

    setting = next((_SETTING_LABELS[l] for l in labels if l in _SETTING_LABELS), None)
    room = room_sentence(labels)
    if setting:
        sentences.append(setting)
    elif room:
        sentences.append(room)

    hallway = "hallway" in labels
    things = [s for s in seen if s.label not in _SETTING_LABELS]
    groups = group_seen(things)
    if groups:
        farthest = max(
            groups, key=lambda g: max((s.distance_m or 0.0) for s in g)
        )
        for group in groups:
            what = describe_group(group)
            where = _where(group, hallway, group is farthest and len(groups) > 1 or (len(groups) == 1 and hallway))
            sentence = f"{where[0].upper() + where[1:]}, {what}." if where else f"{what[0].upper() + what[1:]}."
            sentences.append(sentence)
    elif not sentences:
        sentences.append("I can't make out anything specific right now.")

    sure = [g for g in glimpsed if g.confidence >= _TENTATIVE_MIN_CONFIDENCE and g.label not in labels]
    if sure:
        parts = [f"{with_article(g.label)} {describe_direction(g.azimuth_deg)}" for g in sure[:2]]
        sentences.append(f"I think there may also be {join_spoken(parts)}.")

    reminder = reminder_sentence(scene)
    if reminder:
        sentences.append(reminder)
    return " ".join(sentences)


# What a scan can read off an object beats what its outline suggests. A
# recycling bin and a small refrigerator are the same tall box to the
# detector; the symbol on the front, or the word printed on it, is not.
_BIN_LIKE = {"trash can", "recycling bin", "refrigerator", "cabinet", "box", "dumpster"}
_TEXT_CUES = (
    ("recycl", "recycling bin"),
    ("compost", "compost bin"),
    ("landfill", "trash can"),
    ("trash", "trash can"),
    ("garbage", "trash can"),
    ("rubbish", "trash can"),
    ("waste", "trash can"),
)
# A refrigerator with none of these around it, below the confidence bar,
# is more likely a bin or a cabinet than a fridge in a hallway.
_KITCHEN_COMPANY = {"sink", "microwave", "counter", "dining table", "bowl", "cup", "bottle"}
UNSURE_TALL_BOX = "large cabinet or bin"


def _point_inside(x: float, y: float, box: tuple[float, float, float, float]) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _cue_on(item: Seen, symbols: list[Seen], text_lines) -> str | None:
    """A label the cues on this object's face justify, or None."""
    if item.box is None:
        return None
    for symbol in symbols:
        if symbol.box is None:
            continue
        cx = (symbol.box[0] + symbol.box[2]) / 2
        cy = (symbol.box[1] + symbol.box[3]) / 2
        if _point_inside(cx, cy, item.box):
            return "recycling bin"
    for line in text_lines:
        if not _point_inside(line.left, line.top + line.height / 2, item.box):
            continue
        lowered = line.text.lower()
        for cue, label in _TEXT_CUES:
            if cue in lowered:
                return label
    return None


def refine_labels(seen: list[Seen], text_lines=()) -> list[Seen]:
    """Relabel bin-like objects from the symbol or words on them.

    A recycling symbol on an object makes it a recycling bin whatever the
    detector called it; RECYCLE, COMPOST, TRASH and the like printed on it
    do the same. A lone symbol with no object under it still means a bin
    is there, spoken by direction only. A refrigerator with no kitchen
    around it, below the confidence bar, is spoken as a large cabinet or
    bin rather than as a fridge, since that is what it usually is.
    """
    symbols = [s for s in seen if s.label == "recycling symbol"]
    objects = [s for s in seen if s.label != "recycling symbol"]
    kitchen = any(s.label in _KITCHEN_COMPANY for s in objects)
    claimed: set[int] = set()
    out: list[Seen] = []
    for item in objects:
        label = item.label
        if label in _BIN_LIKE:
            cue = _cue_on(item, symbols, text_lines)
            if cue:
                label = cue
                claimed.update(
                    id(s) for s in symbols
                    if s.box and item.box and _point_inside((s.box[0] + s.box[2]) / 2, (s.box[1] + s.box[3]) / 2, item.box)
                )
            elif label == "refrigerator" and not kitchen and item.confidence < _TENTATIVE_MIN_CONFIDENCE:
                label = UNSURE_TALL_BOX
        out.append(replace(item, label=label))
    for symbol in symbols:
        if id(symbol) not in claimed:
            out.append(replace(symbol, label="recycling bin", distance_m=None))
    return out


def inventory_sentence(seen: list[Seen], glimpsed: Iterable[Seen] = ()) -> str:
    """Everything the scan saw, with confidence, for the screen or a verbose ear."""
    def item(s: Seen) -> str:
        where = f" {_about(s.distance_m)}" if s.distance_m is not None else ""
        return f"{s.label} {round(s.confidence * 100)}%{where}"

    confirmed = ", ".join(item(s) for s in sorted(seen, key=lambda s: -s.confidence))
    once = ", ".join(item(s) for s in sorted(glimpsed, key=lambda s: -s.confidence))
    parts = []
    if confirmed:
        parts.append(f"Seen in both frames: {confirmed}.")
    if once:
        parts.append(f"Seen once: {once}.")
    return " ".join(parts) or "Nothing recognized."
