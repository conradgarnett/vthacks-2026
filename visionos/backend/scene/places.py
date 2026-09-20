"""Memories of places.

Every scan becomes a scene: the things two frames agreed on, the words read
off signs, the kind of room the objects add up to, and a fingerprint of the
picture from CLIP (the model YOLO-World already holds to embed the class
names, so it costs no extra memory). Scenes that look alike are linked into
one place, and a place gets a name the user can say back: Hallway 1,
Office 2.

Recognition is a prediction, and it is spoken only above the user's 80% bar:
"It looks like you are in Hallway 1." A scene that looks like nothing
remembered becomes a new place, spoken as "I'll remember this place as
Hallway 2." In between nothing is said about the place: the scene is kept
unplaced and the panel offers the link to a sighted helper, who can also
rename, delete or merge places. Everything here is deletable, because a
memory that is wrong is worse than none.

The score mixes three kinds of evidence, each of which can be missing: the
picture (cosine similarity of the fingerprints, ramped so a near copy scores
1 and a merely similar room scores 0), the things in view (weighted overlap
of what was seen, people left out because they move) and distinctive words
read in both (a room number is nearly a name). Without a fingerprint, things
alone can never reach the spoken bar: two hallways hold the same doors.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Iterable

from backend.perception.vocabulary import is_obstacle, scale_of
from backend.scene.inference import room_scores

log = logging.getLogger(__name__)

# --- What a scene is made of -----------------------------------------------

# Things that come and go are not part of a place.
_MOVING = frozenset({
    "person", "dog", "cat", "bird", "car", "truck", "bus", "motorcycle",
    "bicycle", "scooter", "shopping cart", "stroller", "wheelchair",
    "suitcase", "backpack",
})
# Structure spoken by direction only: not evidence of which place this is.
_NOT_EVIDENCE = frozenset({"wall"})
# A label that describes the place itself rather than a thing in it.
_SETTING_KIND = {"hallway": "corridor"}
# Evidence a room kind needs before it names a place: the bar the scan uses
# for "this may be an office". Below it the place is just "Place 3".
_NAME_ROOM_SCORE = 2.5
_KIND_NAMES = {"corridor": "Hallway", "dining area": "Dining area", "living room": "Living room"}
# Words read in a scan that help tell one place from another. Wayfinding
# words are printed everywhere and say nothing about which hallway this is.
_COMMON_WORDS = frozenset({
    "exit", "entrance", "entry", "stairs", "stair", "elevator", "lift",
    "restroom", "restrooms", "toilet", "toilets", "washroom", "emergency",
    "fire", "caution", "warning", "danger", "push", "pull", "open", "closed",
    "gate", "platform", "room", "reception", "information", "pharmacy",
    "private", "staff", "authorized", "wet", "floor", "keep", "stop", "only",
    "office", "lobby", "please", "thank", "thanks", "welcome", "hours",
    "sale", "menu", "this", "that", "with", "from", "your", "here", "there",
    "door", "way", "out", "the", "and", "for", "not", "are", "all", "you",
})
_MAX_WORDS = 12

# --- How alike two scenes are -----------------------------------------------

# The picture: cosine similarity of the CLIP fingerprints, ramped. Below the
# low end the picture says nothing; at the high end it is a near copy. Two
# rooms of the same kind sit in the middle, which is why the middle earns
# nothing on its own.
VISUAL_LOW = 0.72
VISUAL_HIGH = 0.92
# The things in view: bigger things are better evidence of where you are; a
# cup was carried in.
_SCALE_WEIGHT = {"large": 1.0, "medium": 0.7, "small": 0.3}
# Words: a shared number (a room number, a bus stop) is nearly a name; a
# shared word is a hint; two scenes that each show numbers and share none
# are probably different doors.
_WORD_WITH_DIGIT = 0.35
_WORD_PLAIN = 0.15
_WORDS_MAX = 0.40
_WORDS_CONFLICT = -0.10
# Weight of the things in view beside the picture, and on their own.
_THINGS_BESIDE_PICTURE = 0.25
_THINGS_ALONE = 0.60

# How many views a place keeps (the newest win) and how many unplaced scenes
# wait for the helper. Memory, not accuracy, sets these.
MAX_SCENES_PER_PLACE = 6
MAX_UNPLACED = 6
# A recognition this recent makes the next one "still in".
STILL_HERE_S = 90.0
# Having just been recognized somewhere is evidence of still being there:
# a place recognized within STAY_S earns this much on the next scan. Measured
# on 60 indoor photos (.claude/bench/place_calib.log): a big move of the
# camera (half the frame, rotated, blurred) leaves the picture at a median
# 0.83, under the bar on its own, while a different room that looks alike
# tops out at 0.87; the bonus lets the second scan of a room count without
# letting a look-alike room in unless it was entered within the minute.
STAY_S = 60.0
STAY_BONUS = 0.15
# How long a recognition counts as where the user is, for questions.
CURRENT_MAX_AGE_S = 120.0


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm > 0 else 0.0


def visual_score(similarity: float) -> float:
    """0 below VISUAL_LOW, 1 at VISUAL_HIGH, straight in between."""
    return max(0.0, min(1.0, (similarity - VISUAL_LOW) / (VISUAL_HIGH - VISUAL_LOW)))


def things_score(a: dict[str, int], b: dict[str, int]) -> float | None:
    """How far the smaller view's things are found in the other, 0 to 1;
    None when either scene saw nothing, since a blank view is no evidence
    against a place.

    Containment rather than overlap on purpose: a step to the side shows a
    third of the same kitchen, and the detector's inventory of one fixed
    view swings by half between scans. A partial view must not read as a
    different place; a view of different things must."""
    if not a or not b:
        return None
    shared = 0.0
    weight_a = 0.0
    weight_b = 0.0
    for label in set(a) | set(b):
        weight = _SCALE_WEIGHT.get(scale_of(label), 0.7)
        shared += weight * min(a.get(label, 0), b.get(label, 0))
        weight_a += weight * a.get(label, 0)
        weight_b += weight * b.get(label, 0)
    smaller = min(weight_a, weight_b)
    return shared / smaller if smaller > 0 else None


def words_score(a: Iterable[str], b: Iterable[str]) -> float:
    first, second = set(a), set(b)
    shared = first & second
    score = sum(_WORD_WITH_DIGIT if any(ch.isdigit() for ch in w) else _WORD_PLAIN for w in shared)
    score = min(score, _WORDS_MAX)
    if not shared and any(any(ch.isdigit() for ch in w) for w in first) and any(
        any(ch.isdigit() for ch in w) for w in second
    ):
        score = _WORDS_CONFLICT
    return score


def distinctive_words(text_lines) -> list[str]:
    """Words read in view worth remembering: numbers, and words of four or
    more letters that are not printed on every wall."""
    out: list[str] = []
    for line in text_lines:
        for token in re.findall(r"[a-z0-9]+", getattr(line, "text", str(line)).lower()):
            has_digit = any(ch.isdigit() for ch in token)
            if has_digit and len(token) < 2:
                continue
            if not has_digit and (len(token) < 4 or token in _COMMON_WORDS):
                continue
            if token not in out:
                out.append(token)
            if len(out) >= _MAX_WORDS:
                return out
    return out


def kind_of(labels: Iterable[str]) -> str | None:
    """The kind of place these things add up to, or None when they do not."""
    labels = list(labels)
    for label, kind in _SETTING_KIND.items():
        if label in labels:
            return kind
    scored = room_scores(labels)
    if scored and scored[0][1] >= _NAME_ROOM_SCORE:
        return scored[0][0]
    return None


def kind_word(kind: str | None) -> str:
    if not kind:
        return "Place"
    return _KIND_NAMES.get(kind, kind[0].upper() + kind[1:])


@dataclass(frozen=True)
class Evidence:
    score: float
    picture: float | None  # raw cosine similarity, None without fingerprints
    things: float | None  # 0..1, None when neither scene saw a thing
    words: float
    recent: float = 0.0  # the stay bonus, when the place was just recognized

    def describe(self) -> str:
        parts = []
        if self.picture is not None:
            parts.append(f"picture {self.picture:.2f}")
        if self.things is not None:
            parts.append(f"things {self.things:.2f}")
        if self.words:
            parts.append(f"words {self.words:+.2f}")
        if self.recent:
            parts.append(f"just here {self.recent:+.2f}")
        return ", ".join(parts) or "no evidence"


@dataclass
class Scene:
    """One remembered view."""

    id: str
    at: float
    labels: dict[str, int] = field(default_factory=dict)
    words: list[str] = field(default_factory=list)
    room: str | None = None
    embedding: list[float] | None = None
    thumbnail: str | None = None
    # Where each thing was, from the user's spot: direction and distance,
    # whether it obstructs, and its size tier. The blueprint draws a place
    # from its views' layouts.
    layout: list[dict] = field(default_factory=list)

    @property
    def has_substance(self) -> bool:
        """Enough to be worth a place of its own: something seen or read."""
        return bool(self.labels) or bool(self.words)

    @property
    def is_empty(self) -> bool:
        return not self.labels and not self.words and self.embedding is None

    def to_dict(self, with_embedding: bool = False) -> dict:
        out = {
            "id": self.id,
            "at": round(self.at, 3),
            "labels": dict(self.labels),
            "words": list(self.words),
            "room": self.room,
            "thumbnail": self.thumbnail,
            "layout": list(self.layout),
        }
        if with_embedding:
            out["embedding"] = None if self.embedding is None else [round(v, 4) for v in self.embedding]
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Scene":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            at=float(data.get("at") or 0.0),
            labels={str(k): int(v) for k, v in (data.get("labels") or {}).items()},
            words=[str(w) for w in (data.get("words") or [])],
            room=data.get("room"),
            embedding=[float(v) for v in data["embedding"]] if data.get("embedding") else None,
            thumbnail=data.get("thumbnail"),
            layout=[dict(item) for item in (data.get("layout") or [])],
        )


def compare(a: Scene, b: Scene) -> Evidence:
    """How alike two scenes are, 0 to 1, with the evidence behind it."""
    picture = cosine(a.embedding, b.embedding) if a.embedding and b.embedding else None
    things = things_score(a.labels, b.labels)
    words = words_score(a.words, b.words)
    if picture is not None:
        # Things seen neither help nor hurt when nothing was seen.
        things_term = 0.5 if things is None else things
        score = visual_score(picture) * (1.0 - _THINGS_BESIDE_PICTURE + _THINGS_BESIDE_PICTURE * things_term) + words
    else:
        score = _THINGS_ALONE * (things or 0.0) + words
    return Evidence(max(0.0, min(1.0, score)), picture, things, words)


def scene_from_scan(seen, text_lines=(), embedding=None, thumbnail=None, now=None) -> Scene:
    """A scene from what a scan agreed on: `seen` carries `.label` (the scan's
    Seen objects or the scene model's tracked ones)."""
    labels = Counter(
        s.label for s in seen if s.label not in _MOVING and s.label not in _NOT_EVIDENCE
    )
    layout = [
        {
            "label": s.label,
            "azimuth_deg": round(float(s.azimuth_deg), 1),
            "distance_m": None if s.distance_m is None else round(float(s.distance_m), 1),
            "obstacle": is_obstacle(s.label),
            "scale": scale_of(s.label),
        }
        for s in seen
        if s.label not in _NOT_EVIDENCE
    ]
    return Scene(
        id=uuid.uuid4().hex[:8],
        at=time.time() if now is None else now,
        labels=dict(labels),
        words=distinctive_words(text_lines),
        room=kind_of(labels.elements()),
        embedding=list(embedding) if embedding else None,
        thumbnail=thumbnail,
        layout=layout,
    )


def thumbnail_of(image_bgr, width: int = 160) -> str | None:
    """A small JPEG of the frame as a data URL, for the panel. None on any trouble."""
    try:
        import cv2

        height, full_width = image_bgr.shape[:2]
        if full_width <= 0 or height <= 0:
            return None
        small = cv2.resize(
            image_bgr, (width, max(1, round(height * width / full_width))), interpolation=cv2.INTER_AREA
        )
        ok, buffer = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
        if not ok:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")
    except Exception:
        return None


@dataclass
class Place:
    id: str
    name: str
    kind: str | None
    created_at: float
    scenes: list[Scene] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "created_at": round(self.created_at, 3),
            "scenes": [s.to_dict() for s in self.scenes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Place":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            name=str(data.get("name") or "Place"),
            kind=data.get("kind"),
            created_at=float(data.get("created_at") or 0.0),
            scenes=[Scene.from_dict(s) for s in data.get("scenes") or []],
        )


@dataclass
class Observation:
    """What the memory made of a scan."""

    kind: str  # recognized | new | unsure | skipped
    scene: Scene | None
    place: Place | None = None
    candidate: Place | None = None
    evidence: Evidence | None = None
    still: bool = False

    @property
    def score(self) -> float:
        return self.evidence.score if self.evidence else 0.0

    @property
    def spoken_prefix(self) -> str | None:
        if self.kind != "recognized" or self.place is None:
            return None
        return f"It looks like you are {'still ' if self.still else ''}in {self.place.name}."

    @property
    def spoken_suffix(self) -> str | None:
        if self.kind == "new" and self.place is not None:
            return f"I'll remember this place as {self.place.name}."
        return None

    def with_speech(self, description: str) -> str:
        return " ".join(part for part in (self.spoken_prefix, description, self.spoken_suffix) if part)

    def to_event(self) -> dict:
        def brief(place: Place | None) -> dict | None:
            return None if place is None else {"id": place.id, "name": place.name}

        return {
            "type": "place",
            "kind": self.kind,
            "score": round(self.score, 2),
            "place": brief(self.place),
            "candidate": brief(self.candidate),
            "scene_id": self.scene.id if self.scene else None,
            "evidence": self.evidence.describe() if self.evidence else "",
            "spoken": self.spoken_prefix or self.spoken_suffix,
        }


class PlaceMemory:
    """The places this machine remembers, on disk between runs."""

    def __init__(
        self,
        path: str | None = None,
        match_confidence: float = 0.80,
        new_below: float = 0.50,
        max_scenes_per_place: int = MAX_SCENES_PER_PLACE,
        max_unplaced: int = MAX_UNPLACED,
    ) -> None:
        self.path = path
        self.match_confidence = match_confidence
        self.new_below = new_below
        self.max_scenes_per_place = max_scenes_per_place
        self.max_unplaced = max_unplaced
        self.places: list[Place] = []
        self.unplaced: list[Scene] = []
        # The last recognition: place id, score, when.
        self.current: tuple[str, float, float] | None = None
        self._load()

    # --- Persistence ------------------------------------------------------

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.places = [Place.from_dict(p) for p in data.get("places") or []]
            self.unplaced = [Scene.from_dict(s) for s in data.get("unplaced") or []]
            log.info(
                "Places: remembered %d place(s), %d scene(s) from %s",
                len(self.places), sum(len(p.scenes) for p in self.places), self.path,
            )
        except Exception:
            log.exception("Places: could not read %s; starting with none", self.path)
            self.places, self.unplaced = [], []

    def save(self) -> None:
        if not self.path:
            return
        data = {
            "version": 1,
            "saved_at": time.time(),
            "places": [
                {**p.to_dict(), "scenes": [s.to_dict(with_embedding=True) for s in p.scenes]}
                for p in self.places
            ],
            "unplaced": [s.to_dict(with_embedding=True) for s in self.unplaced],
        }
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        # Written whole, then swapped in: a crash mid-write must not eat the memory.
        fd, temp = tempfile.mkstemp(prefix=".places-", suffix=".json", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(temp, self.path)
        except Exception:
            log.exception("Places: could not write %s", self.path)
            try:
                os.unlink(temp)
            except OSError:
                pass

    # --- Lookup -----------------------------------------------------------

    def place(self, place_id: str) -> Place | None:
        return next((p for p in self.places if p.id == place_id), None)

    def find_scene(self, scene_id: str) -> tuple[Scene, Place | None] | None:
        for place in self.places:
            for scene in place.scenes:
                if scene.id == scene_id:
                    return scene, place
        for scene in self.unplaced:
            if scene.id == scene_id:
                return scene, None
        return None

    def best_match(self, scene: Scene) -> tuple[Place | None, Evidence | None]:
        """The place this scene most resembles, judged by its best view."""
        best: tuple[Place | None, Evidence | None] = (None, None)
        for place in self.places:
            for known in place.scenes:
                evidence = compare(scene, known)
                if best[1] is None or evidence.score > best[1].score:
                    best = (place, evidence)
        return best

    def next_name(self, kind: str | None) -> str:
        word = kind_word(kind)
        pattern = re.compile(rf"^{re.escape(word)} (\d+)$")
        taken = [int(m.group(1)) for p in self.places if (m := pattern.match(p.name))]
        return f"{word} {max(taken, default=0) + 1}"

    def current_place(self, now: float | None = None, max_age_s: float = CURRENT_MAX_AGE_S) -> Place | None:
        """Where the user was last recognized to be, while that is recent."""
        if self.current is None:
            return None
        place_id, score, at = self.current
        now = time.time() if now is None else now
        if now - at > max_age_s or score < self.match_confidence:
            return None
        return self.place(place_id)

    def current_name(self, now: float | None = None) -> str | None:
        place = self.current_place(now)
        return place.name if place else None

    # --- Remembering ------------------------------------------------------

    def observe(self, scene: Scene, now: float | None = None) -> Observation:
        """File a scan's scene: recognized, new, unsure or skipped."""
        now = time.time() if now is None else now
        if scene.is_empty:
            return Observation("skipped", None)

        place, evidence = self.best_match(scene)
        if (
            place is not None
            and evidence is not None
            and self.current is not None
            and self.current[0] == place.id
            and now - self.current[2] <= STAY_S
        ):
            evidence = replace(evidence, score=min(1.0, evidence.score + STAY_BONUS), recent=STAY_BONUS)
        if place is not None and evidence is not None and evidence.score >= self.match_confidence:
            still = (
                self.current is not None
                and self.current[0] == place.id
                and now - self.current[2] <= STILL_HERE_S
            )
            self._attach(place, scene)
            self.current = (place.id, evidence.score, now)
            self.save()
            log.info("place: recognized %s (%.2f; %s)", place.name, evidence.score, evidence.describe())
            return Observation("recognized", scene, place=place, evidence=evidence, still=still)

        if place is None or evidence is None or evidence.score < self.new_below:
            if not scene.has_substance:
                # A blank view is not worth a place of its own.
                return Observation("skipped", scene, evidence=evidence)
            created = self.remember_new(scene, now=now)
            self.current = (created.id, 1.0, now)
            log.info(
                "place: new %s (best other %.2f)", created.name, evidence.score if evidence else 0.0
            )
            return Observation("new", scene, place=created, evidence=evidence)

        self.unplaced.append(scene)
        del self.unplaced[: -self.max_unplaced]
        self.save()
        log.info("place: unsure, might be %s (%.2f; %s)", place.name, evidence.score, evidence.describe())
        return Observation("unsure", scene, candidate=place, evidence=evidence)

    def remember_new(self, scene: Scene, name: str | None = None, now: float | None = None) -> Place:
        now = time.time() if now is None else now
        place = Place(
            id=uuid.uuid4().hex[:8],
            name=name or self.next_name(scene.room),
            kind=scene.room,
            created_at=now,
            scenes=[scene],
        )
        self.places.append(place)
        self.save()
        return place

    def _attach(self, place: Place, scene: Scene) -> None:
        place.scenes.append(scene)
        del place.scenes[: -self.max_scenes_per_place]

    # --- The helper's edits -----------------------------------------------

    def rename(self, place_id: str, name: str) -> Place | None:
        place = self.place(place_id)
        name = " ".join(name.split())
        if place is None or not name:
            return None
        place.name = name[:60]
        self.save()
        return place

    def delete_place(self, place_id: str) -> Place | None:
        place = self.place(place_id)
        if place is None:
            return None
        self.places.remove(place)
        if self.current and self.current[0] == place_id:
            self.current = None
        self.save()
        return place

    def delete_scene(self, scene_id: str) -> tuple[Scene, Place | None] | None:
        """Forget one view. A place left with no views goes with it."""
        found = self.find_scene(scene_id)
        if found is None:
            return None
        scene, place = found
        if place is None:
            self.unplaced.remove(scene)
        else:
            place.scenes.remove(scene)
            if not place.scenes:
                self.delete_place(place.id)
        self.save()
        return scene, place

    def link(self, scene_id: str, place_id: str | None, name: str | None = None) -> Place | None:
        """Put a view into a place, or start a new place from it."""
        found = self.find_scene(scene_id)
        if found is None:
            return None
        scene, holder = found
        target = self.place(place_id) if place_id else None
        if place_id and target is None:
            return None
        if holder is target and target is not None:
            return target
        if holder is None:
            self.unplaced.remove(scene)
        else:
            holder.scenes.remove(scene)
        if target is None:
            target = self.remember_new(scene, name=name)
        else:
            self._attach(target, scene)
        if holder is not None and not holder.scenes:
            self.delete_place(holder.id)
        self.save()
        return target

    def merge(self, source_id: str, into_id: str) -> Place | None:
        """Every view of one place becomes a view of another; the first is gone."""
        source, target = self.place(source_id), self.place(into_id)
        if source is None or target is None or source is target:
            return None
        for scene in source.scenes:
            self._attach(target, scene)
        self.places.remove(source)
        if self.current and self.current[0] == source_id:
            self.current = (target.id, self.current[1], self.current[2])
        self.save()
        return target

    def forget_all(self) -> int:
        count = len(self.places)
        self.places, self.unplaced, self.current = [], [], None
        self.save()
        return count

    # --- For the panel ----------------------------------------------------

    def to_dict(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        current = None
        if self.current is not None:
            place = self.place(self.current[0])
            if place is not None:
                current = {
                    "place_id": place.id,
                    "name": place.name,
                    "score": round(self.current[1], 2),
                    "age_s": round(max(0.0, now - self.current[2]), 1),
                    "fresh": self.current_place(now) is not None,
                }
        return {
            "enabled": True,
            "places": [p.to_dict() for p in self.places],
            "unplaced": [s.to_dict() for s in self.unplaced],
            "current": current,
            "match_confidence": self.match_confidence,
            "new_below": self.new_below,
        }
