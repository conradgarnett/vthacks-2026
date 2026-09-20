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

from backend.perception.geometry import clock_position, describe_direction, lateral_offset_m
from backend.perception.vocabulary import LANDMARK_CLASSES, is_obstacle, is_small
from backend.scene.model import SceneModel, SceneObject
from backend.scene.queries import summarize
from backend.speech.phrasing import join_spoken, pluralize, with_article

# Which objects imply which kind of space, and how strongly. Weights are
# evidence, not probabilities: a bed alone settles "bedroom"; a chair alone
# settles nothing, because chairs are everywhere.
ROOMS: list[tuple[str, dict[str, float]]] = [
    ("kitchen", {"refrigerator": 2.0, "microwave": 1.5, "oven": 1.5, "stove": 1.5,
                 "dishwasher": 1.5, "sink": 1.0, "counter": 1.0, "kettle": 0.5,
                 "toaster": 0.5, "coffee maker": 0.5, "bowl": 0.5, "cup": 0.3,
                 "bottle": 0.3, "pot": 0.3, "pan": 0.3}),
    ("bathroom", {"toilet": 3.0, "bathtub": 3.0, "sink": 1.0, "toilet paper": 1.0,
                  "toothbrush": 1.0, "towel": 0.5, "hair dryer": 0.5, "mirror": 0.3}),
    ("bedroom", {"bed": 3.0, "wardrobe": 1.5, "nightstand": 1.5, "dresser": 1.0}),
    ("office", {"desk": 1.5, "laptop": 1.5, "keyboard": 1.0, "printer": 1.0,
                "computer mouse": 0.7, "bookshelf": 0.5, "chair": 0.5, "book": 0.3}),
    ("dining area", {"dining table": 2.0, "table": 1.5, "chair": 0.5, "cup": 0.3, "bowl": 0.3,
                     "plate": 0.3, "fork": 0.2, "spoon": 0.2}),
    ("living room", {"couch": 2.0, "fireplace": 2.0, "tv": 1.5, "coffee table": 1.5,
                     "armchair": 1.0, "potted plant": 0.5, "remote": 0.5, "lamp": 0.3,
                     "chair": 0.3}),
    ("corridor", {"hallway": 2.0, "door": 1.0, "doorway": 1.0, "exit sign": 1.5, "elevator": 1.5,
                  "escalator": 1.5, "handrail": 1.0, "stairs": 1.0, "staircase": 1.0,
                  "fire extinguisher": 1.0, "vending machine": 1.0, "drinking fountain": 1.0}),
    ("street", {"bus": 2.0, "crosswalk": 2.0, "car": 1.5, "truck": 1.5, "traffic light": 1.5,
                "stop sign": 1.5, "fire hydrant": 1.5, "motorcycle": 1.0, "curb": 1.0,
                "bollard": 1.0, "parking meter": 1.0, "bicycle": 0.7, "pole": 0.7,
                "traffic cone": 0.7, "mailbox": 0.7, "scooter": 0.5, "tree": 0.5}),
]
# Evidence needed before a room is named. A fridge plus a sink, a bed
# alone or a toilet alone reach it; a door plus a handrail do not. There
# used to be a lower tier spoken as "this may be", but in live use the
# user heard guesses that were plainly wrong, and a wrong guess is not
# made harmless by hedging it. Below this score the room is not named.
_ROOM_MIN_SCORE = 3.0
# Below that, a room is offered as "this may be" only when the evidence is
# fairly strong and clearly ahead of any other room: the user asked for
# every scan to start with what the place seems to be, and also, earlier,
# never to hear a room that turns out wrong. A desk and a keyboard reach
# this; a door and a handrail do not.
_ROOM_HEDGE_SCORE = 2.5
_ROOM_HEDGE_MARGIN = 1.0
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


def room_scores(labels: Iterable[str]) -> list[tuple[str, float]]:
    """Every room's evidence score for these objects, best first."""
    counts = Counter(labels)
    scored = []
    for room, weights in ROOMS:
        score = sum(
            weights.get(label, 0.0) * min(count, _MAX_COUNT_PER_LABEL)
            for label, count in counts.items()
        )
        scored.append((room, score))
    scored.sort(key=lambda rs: -rs[1])
    return scored


def room_guess(labels: Iterable[str]) -> tuple[str, float] | None:
    """The kind of space these objects add up to, with its evidence score,
    when the evidence is decisive."""
    best = room_scores(labels)[0]
    return best if best[1] >= _ROOM_MIN_SCORE else None


def room_sentence(labels: Iterable[str]) -> str | None:
    """"This looks like a kitchen." on decisive evidence, "This may be a
    kitchen." on fairly strong evidence with no close rival, else nothing."""
    scored = room_scores(labels)
    if not scored:
        return None
    (room, score), runner_up = scored[0], (scored[1][1] if len(scored) > 1 else 0.0)
    if score >= _ROOM_MIN_SCORE:
        return f"This looks like {with_article(room)}."
    if score >= _ROOM_HEDGE_SCORE and score - runner_up >= _ROOM_HEDGE_MARGIN:
        return f"This may be {with_article(room)}."
    return None


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
        # A possible cup is never worth a mention; a real one is spoken
        # only when asked about. A wall is never a hint.
        if is_small(detection.label) or detection.label in _STRUCTURE_LABELS:
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

    sentences.append(walkway_sentence(
        [Seen(o.label, o.confidence, o.azimuth_deg, o.distance_m) for o in scene.all_objects() if o.visible]
    ))
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
# Structure: spoken only by the walkway sentence, never as a thing in view.
_STRUCTURE_LABELS = {"wall"}
# Furniture a person sits at or in.
_TABLES = {"table", "dining table", "desk", "counter", "coffee table"}
_SEATS = {"chair", "armchair", "stool", "couch", "bench"}
# Two things within this angle and distance of each other belong to one group.
_GROUP_AZIMUTH_DEG = 20.0
_GROUP_DISTANCE_RATIO = 1.6
_GROUP_DISTANCE_M = 1.5
# A person's box overlapping a seat's by this much means they are in it.
_SITTING_OVERLAP = 0.05
# What a person is doing, read from the object in their hands or in front
# of them. Spoken as "it looks like" and only when the detector was sure
# of both the person and the object, since it is a prediction, not a
# sighting. The object counts as theirs when its centre falls inside the
# person's box widened by this fraction on each side.
_ACTIVITIES = (
    ("laptop", "using a laptop"),
    ("keyboard", "typing at a keyboard"),
    ("cell phone", "on their phone"),
    ("book", "reading"),
    ("cup", "having a drink"),
    ("drinking glass", "having a drink"),
    ("bowl", "eating"),
    ("fork", "eating"),
    ("spoon", "eating"),
    ("toothbrush", "brushing their teeth"),
)
_IN_REACH = 0.25
# What a person is doing, read from the furniture they are at rather than
# an object in hand. Same bar: both above 0.8, the person on or at it.
_FURNITURE_ACTIVITIES = (
    ("desk", "working at a desk"),
    ("counter", "at a counter"),
    ("couch", "sitting on a couch"),
    ("bed", "lying on a bed"),
    ("sink", "at a sink"),
    ("stove", "cooking at a stove"),
)
# Words worth reading out from a scan even when nobody asked for text:
# wayfinding and safety. Anything printed on a sign or a door counts too.
_NOTABLE_WORDS = {
    "exit", "entrance", "entry", "stairs", "stair", "elevator", "lift",
    "restroom", "restrooms", "toilet", "toilets", "washroom", "emergency",
    "fire", "caution", "warning", "danger", "push", "pull", "open",
    "closed", "gate", "platform", "room", "reception", "information",
    "pharmacy", "way", "out", "private", "staff", "authorized", "wet",
    "floor", "keep", "stop", "no", "only", "office", "lobby",
}
_SIGN_LIKE = {"sign", "exit sign", "door", "doorway", "elevator"}
_MAX_NOTABLE = 2
# The walkway: a strip this wide and this long straight ahead, the same
# corridor the path question checks. Something known to be walked into,
# inside it, blocks it; the nearest thing of size within this angle of
# straight ahead is what the way leads to.
_WALK_WIDTH_M = 1.0
_WALK_DISTANCE_M = 3.0
_AHEAD_DEG = 15.0


def _about(distance_m: float | None) -> str:
    if distance_m is None:
        return ""
    if distance_m < 1.0:
        return "less than a meter"
    if distance_m < 3.0:
        value = round(distance_m * 2) / 2
        return f"about {value:g} {'meter' if value == 1 else 'meters'}"
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


def _in_reach(person: Seen, item: Seen) -> bool:
    if person.box is None or item.box is None:
        return False
    x1, y1, x2, y2 = person.box
    dx, dy = (x2 - x1) * _IN_REACH, (y2 - y1) * _IN_REACH
    cx, cy = (item.box[0] + item.box[2]) / 2, (item.box[1] + item.box[3]) / 2
    return x1 - dx <= cx <= x2 + dx and y1 - dy <= cy <= y2 + dy


def _activity(people: list[Seen], items: list[Seen]) -> tuple[str, Seen] | None:
    """The first activity a sure person and a sure object in reach justify."""
    for person in people:
        if person.confidence < _TENTATIVE_MIN_CONFIDENCE:
            continue
        for label, doing in _ACTIVITIES:
            for item in items:
                if item.label == label and item.confidence >= _TENTATIVE_MIN_CONFIDENCE and _in_reach(person, item):
                    return doing, item
    return None


def _furniture_activity(people: list[Seen], furniture: list[Seen]) -> tuple[str, Seen] | None:
    """The first place a sure person is at or on, with what that implies."""
    for person in people:
        if person.confidence < _TENTATIVE_MIN_CONFIDENCE:
            continue
        for label, doing in _FURNITURE_ACTIVITIES:
            for item in furniture:
                if item.label != label or item.confidence < _TENTATIVE_MIN_CONFIDENCE:
                    continue
                if _in_reach(person, item) or (
                    person.box and item.box and _overlap(person.box, item.box) >= _SITTING_OVERLAP
                ):
                    return doing, item
    return None


def _text_direction(left: float) -> str:
    if left < 0.33:
        return "to your left"
    if left > 0.66:
        return "to your right"
    return "ahead"


def notable_text(text_lines, seen: list[Seen]) -> list[str]:
    """Sentences for the words in view worth saying unasked.

    A line is notable when it carries a wayfinding or safety word, or
    when it sits on something sign-like (a sign, a door). Anything else
    in view is left for a Read. At most two, nearest the middle first.
    """
    signs = [s for s in seen if s.label in _SIGN_LIKE and s.box is not None]
    picked: list[tuple[float, str]] = []
    spoken: set[str] = set()
    for line in text_lines:
        text = " ".join(line.text.split())
        key = text.lower()
        if len(key) < 2 or key in spoken:
            continue
        words = {w.strip(".,:;!").lower() for w in text.split()}
        on_sign = next(
            (s for s in signs if _point_inside(line.left, line.top + line.height / 2, s.box)), None
        )
        if not (words & _NOTABLE_WORDS) and on_sign is None:
            continue
        spoken.add(key)
        where = _text_direction(line.left)
        if on_sign is not None:
            sentence = f"The {on_sign.label} {where} says {text}."
        else:
            sentence = f"A sign {where} says {text}."
        picked.append((abs(line.left + 0.1 - 0.5), sentence))
    picked.sort(key=lambda pair: pair[0])
    return [sentence for _, sentence in picked[:_MAX_NOTABLE]]


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
        # Hand-held things are left for a question: "are there any cups on
        # the table" gets them, a scan does not.
        shown = [s for s in group if not is_small(s.label)]
        return _counted(shown) if shown else ""

    subject = "a person" if len(people) == 1 else pluralize(len(people), "person")
    sitting = any(_is_sitting(p, seats) for p in people)
    verb = "sitting" if sitting else "standing"
    phrase = subject

    activity = _activity(people, rest)
    placed = None if activity else _furniture_activity(people, [*tables, *seats, *rest])
    # A phone in someone's hand says what they are doing; a phone on the
    # table is not mentioned unless asked about.
    rest = [s for s in rest if not is_small(s.label)]
    if activity or placed:
        doing, item = activity or placed
        rest = [s for s in rest if s is not item]
        tables = [s for s in tables if s is not item]
        seats = [s for s in seats if s is not item]
        phrase = f"it looks like {subject} {doing}"
        if activity and tables:
            phrase += f" at {with_article(tables[0].label)}"
        elif placed and tables:
            phrase += f", with {_counted(tables)}"
        extra_seats = len(seats) - (len(people) if sitting else 0)
        if extra_seats > 0:
            phrase += f", with {pluralize(extra_seats, seats[0].label) if extra_seats > 1 else 'an empty ' + seats[0].label}"
            if extra_seats > 1:
                phrase += " empty"
        if rest:
            phrase += f", and {_counted(rest)}"
        return phrase

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


def walkway_sentence(seen: list[Seen]) -> str:
    """The last thing a scan says: whether the way straight ahead is clear,
    and what it leads to.

    Only obstacles the detector recognizes can block it, so a clear way is
    always hedged: "as far as I can tell". A thing with no distance (stairs,
    an escalator) straight ahead is named without one rather than dropped,
    since those are the things a foot finds first.
    """
    if not seen:
        return "I can't tell whether the way ahead is clear."
    ahead = [s for s in seen if abs(s.azimuth_deg) <= _AHEAD_DEG]
    blockers = [
        s for s in seen
        if is_obstacle(s.label) and s.distance_m is not None and s.distance_m <= _WALK_DISTANCE_M
        and lateral_offset_m(s.azimuth_deg, s.distance_m) <= _WALK_WIDTH_M / 2
    ]
    if blockers:
        nearest = min(blockers, key=lambda s: s.distance_m or 0.0)
        where = f"{_about(nearest.distance_m)} {describe_direction(nearest.azimuth_deg)}".strip()
        return f"The way ahead is blocked by {with_article(nearest.label)} {where}."

    unplaced = [
        s for s in ahead
        if s.distance_m is None and is_obstacle(s.label) and s.label not in _STRUCTURE_LABELS
    ]
    if unplaced:
        return (
            f"Straight ahead there {'are' if unplaced[0].label.endswith('s') else 'is'} "
            f"{with_article(unplaced[0].label)}; I can't tell how far."
        )

    targets = sorted(
        (s for s in ahead if s.distance_m is not None and not is_small(s.label)),
        key=lambda s: s.distance_m or 0.0,
    )
    if targets:
        target = targets[0]
        return (
            f"The way ahead looks clear, as far as I can tell, and leads to "
            f"{with_article(target.label)} {_about(target.distance_m)} ahead."
        )
    if any(s.label == "wall" for s in ahead):
        return "The way ahead looks clear, as far as I can tell, and leads to a wall; I can't tell how far."
    if any(s.label == "hallway" for s in seen):
        return "The way ahead looks clear down the hallway, as far as I can tell."
    return "The way ahead looks clear as far as I can tell, but I can't see what it leads to."


def describe_scan(
    scene: SceneModel, seen: list[Seen], glimpsed: Iterable[Seen] = (), text_lines=()
) -> str:
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
    things = [s for s in seen if s.label not in _SETTING_LABELS and s.label not in _STRUCTURE_LABELS]
    # Small things still group, since a cup in reach says what a person is
    # doing, but a group with nothing bigger in it is not spoken.
    groups = [g for g in group_seen(things) if any(not is_small(s.label) for s in g)]
    # The user's order: the place, then people and what they are doing,
    # then everything else. Nearest first within each.
    groups.sort(key=lambda g: not any(s.label == "person" for s in g))
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

    sentences.extend(notable_text(text_lines, seen))

    sure = [
        g for g in glimpsed
        if g.confidence >= _TENTATIVE_MIN_CONFIDENCE and g.label not in labels
        and not is_small(g.label) and g.label not in _STRUCTURE_LABELS
    ]
    if sure:
        parts = [f"{with_article(g.label)} {describe_direction(g.azimuth_deg)}" for g in sure[:2]]
        sentences.append(f"I think there may also be {join_spoken(parts)}.")

    reminder = reminder_sentence(scene)
    if reminder:
        sentences.append(reminder)
    # Last, every time: the user's rule. Where the feet go next.
    sentences.append(walkway_sentence(seen))
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
_KITCHEN_COMPANY = {
    "sink", "microwave", "counter", "dining table", "bowl", "cup", "bottle",
    "stove", "oven", "dishwasher", "kettle", "toaster", "coffee maker",
}
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
