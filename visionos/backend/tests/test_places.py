"""Memories of places: scenes linked into named rooms, recognition spoken
only above the user's 80% bar, a new place remembered aloud, and everything
deletable from the panel."""

from __future__ import annotations

import json
import math
from collections import Counter

import pytest

from backend.ai.ocr import TextLine
from backend.main import (
    PlaceMerge,
    PlaceName,
    SceneLink,
    Session,
    app,
    places_delete,
    places_forget_all,
    places_index,
    places_merge,
    places_rename,
    scenes_delete,
    scenes_link,
)
from backend.perception.detector import Detection
from backend.perception.geometry import BoundingBox
from backend.scene.inference import Seen
from backend.scene.places import (
    VISUAL_HIGH,
    VISUAL_LOW,
    PlaceMemory,
    Scene,
    compare,
    distinctive_words,
    kind_of,
    scene_from_scan,
    things_score,
    visual_score,
    words_score,
)
from backend.tests.test_peek import FakeProvider, FakeSocket, frame
from backend.tests.test_scan import FakeDetector, FakePerception, NullReader

HALLWAY = ["hallway", "door", "door", "trash can"]
OFFICE = ["desk", "laptop", "chair", "keyboard"]


def unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def at_cosine(similarity: float) -> tuple[list[float], list[float]]:
    """Two unit vectors whose cosine similarity is exactly `similarity`."""
    return unit(1.0, 0.0), unit(similarity, math.sqrt(max(0.0, 1.0 - similarity**2)))


def scene(labels=(), words=(), embedding=None, at=0.0, sid="s1") -> Scene:
    counts = dict(Counter(labels))
    return Scene(id=sid, at=at, labels=counts, words=list(words), room=kind_of(counts), embedding=embedding)


def text(line: str) -> TextLine:
    return TextLine(text=line, confidence=0.9, top=0.3, left=0.1, height=0.05)


# --- Naming and the first scan ------------------------------------------------


class TestRemembering:
    def test_a_first_scan_becomes_a_place_named_by_its_kind_and_is_said_aloud(self):
        memory = PlaceMemory()
        result = memory.observe(scene(HALLWAY, embedding=unit(1, 0)))
        assert result.kind == "new"
        assert result.place.name == "Hallway 1"
        assert result.spoken_prefix is None
        assert result.spoken_suffix == "I'll remember this place as Hallway 1."
        assert result.with_speech("You're looking down a hallway.") == (
            "You're looking down a hallway. I'll remember this place as Hallway 1."
        )

    def test_kinds_name_places_and_nothing_decisive_is_just_a_place(self):
        assert kind_of(HALLWAY) == "corridor"
        assert kind_of(OFFICE) == "office"
        assert kind_of(["chair"]) is None
        memory = PlaceMemory()
        assert memory.observe(scene(OFFICE, embedding=unit(0, 1))).place.name == "Office 1"
        assert memory.observe(scene(["chair"], embedding=unit(0, 0, 1))).place.name == "Place 1"

    def test_numbers_count_up_per_kind_and_are_not_reused_below_a_higher_one(self):
        memory = PlaceMemory()
        for index in range(3):
            vector = [0.0] * 4
            vector[index] = 1.0
            memory.observe(scene(HALLWAY, embedding=vector))
        assert [p.name for p in memory.places] == ["Hallway 1", "Hallway 2", "Hallway 3"]
        memory.delete_place(memory.places[1].id)
        assert memory.next_name("corridor") == "Hallway 4"
        assert memory.next_name("office") == "Office 1"

    def test_the_same_view_again_is_recognized_and_then_still_there(self):
        memory = PlaceMemory()
        memory.observe(scene(HALLWAY, embedding=unit(1, 0)), now=0.0)
        again = memory.observe(scene(HALLWAY, embedding=unit(1, 0), sid="s2"), now=10.0)
        assert again.kind == "recognized"
        assert again.score >= 0.8
        assert again.spoken_prefix == "It looks like you are still in Hallway 1."
        later = memory.observe(scene(HALLWAY, embedding=unit(1, 0), sid="s3"), now=500.0)
        assert later.spoken_prefix == "It looks like you are in Hallway 1."
        assert len(memory.places) == 1 and len(memory.places[0].scenes) == 3

    def test_a_different_room_starts_a_second_place(self):
        memory = PlaceMemory()
        memory.observe(scene(HALLWAY, embedding=unit(1, 0)))
        result = memory.observe(scene(OFFICE, embedding=unit(0, 1), sid="s2"))
        assert result.kind == "new" and result.place.name == "Office 1"
        assert [p.name for p in memory.places] == ["Hallway 1", "Office 1"]


# --- The 80% bar -----------------------------------------------------------------


class TestTheBar:
    def test_a_match_just_under_80_percent_is_not_spoken_and_waits_unplaced(self):
        memory = PlaceMemory()
        first, second = at_cosine(VISUAL_LOW + 0.79 * (VISUAL_HIGH - VISUAL_LOW))
        # Minutes apart, so having just been there does not count.
        memory.observe(scene(HALLWAY, embedding=first), now=0.0)
        result = memory.observe(scene(HALLWAY, embedding=second, sid="s2"), now=500.0)
        assert result.kind == "unsure"
        assert 0.78 <= result.score < 0.80
        assert result.candidate.name == "Hallway 1"
        assert result.spoken_prefix is None and result.spoken_suffix is None
        assert result.with_speech("The picture.") == "The picture."
        assert [s.id for s in memory.unplaced] == ["s2"]
        assert len(memory.places[0].scenes) == 1

    def test_a_match_at_80_percent_is_spoken(self):
        memory = PlaceMemory()
        first, second = at_cosine(VISUAL_LOW + 0.81 * (VISUAL_HIGH - VISUAL_LOW))
        memory.observe(scene(HALLWAY, embedding=first))
        result = memory.observe(scene(HALLWAY, embedding=second, sid="s2"))
        assert result.kind == "recognized" and result.score >= 0.8

    def test_a_merely_similar_picture_of_a_different_room_is_a_new_place(self):
        memory = PlaceMemory()
        first, second = at_cosine(VISUAL_LOW)
        memory.observe(scene(HALLWAY, embedding=first))
        result = memory.observe(scene(OFFICE, embedding=second, sid="s2"))
        assert result.kind == "new" and result.place.name == "Office 1"

    def test_without_a_picture_the_same_things_alone_never_reach_the_bar(self):
        memory = PlaceMemory()
        memory.observe(scene(HALLWAY), now=0.0)
        result = memory.observe(scene(HALLWAY, sid="s2"), now=500.0)
        assert result.kind == "unsure"
        assert result.score == pytest.approx(0.6)
        assert result.spoken_prefix is None

    def test_having_just_been_somewhere_helps_a_moved_view_but_not_forever(self):
        memory = PlaceMemory()
        similarity = VISUAL_LOW + 0.70 * (VISUAL_HIGH - VISUAL_LOW)
        rest = math.sqrt(1.0 - similarity**2)
        first = unit(1.0, 0.0, 0.0)
        second = unit(similarity, rest, 0.0)
        # As far from the first view as the second, and no closer to the
        # second view than two different rooms are.
        third = unit(similarity, 0.0, rest)
        memory.observe(scene(HALLWAY, embedding=first), now=0.0)
        # Raw 0.70 plus the stay bonus clears the bar while the last
        # recognition is fresh; the evidence says so.
        soon = memory.observe(scene(HALLWAY, embedding=second, sid="s2"), now=20.0)
        assert soon.kind == "recognized" and soon.evidence.recent == pytest.approx(0.15)
        assert "just here +0.15" in soon.evidence.describe()
        assert soon.spoken_prefix == "It looks like you are still in Hallway 1."
        # Minutes later the same raw score is only a maybe.
        later = memory.observe(scene(HALLWAY, embedding=third, sid="s3"), now=400.0)
        assert later.kind == "unsure" and later.evidence.recent == 0.0

    def test_the_bonus_never_goes_to_a_different_place(self):
        memory = PlaceMemory()
        memory.observe(scene(HALLWAY, embedding=unit(1, 0)), now=0.0)
        memory.observe(scene(OFFICE, embedding=unit(0, 1), sid="o1"), now=5.0)
        # The office was just recognized; a borderline hallway view gets nothing for it.
        _, second = at_cosine(VISUAL_LOW + 0.70 * (VISUAL_HIGH - VISUAL_LOW))
        result = memory.observe(scene(HALLWAY, embedding=second, sid="h2"), now=10.0)
        assert result.kind == "unsure" and result.candidate.name == "Hallway 1"
        assert result.evidence.recent == 0.0

    def test_a_shared_room_number_lifts_a_borderline_picture_over_the_bar(self):
        memory = PlaceMemory()
        first, second = at_cosine(VISUAL_LOW + 0.65 * (VISUAL_HIGH - VISUAL_LOW))
        memory.observe(scene(HALLWAY, words=["204b"], embedding=first), now=0.0)
        without = memory.observe(scene(HALLWAY, embedding=second, sid="s2"), now=500.0)
        assert without.kind == "unsure"
        with_number = memory.observe(scene(HALLWAY, words=["204b"], embedding=second, sid="s3"), now=1000.0)
        assert with_number.kind == "recognized"

    def test_different_numbers_pull_scenes_apart_and_wayfinding_words_do_not_count(self):
        assert words_score(["204"], ["206"]) == pytest.approx(-0.10)
        assert words_score(["204b", "lobby"], ["204b", "lobby"]) == pytest.approx(0.40)
        assert words_score([], ["204"]) == 0.0
        assert distinctive_words([text("EXIT"), text("PUSH TO OPEN")]) == []
        assert distinctive_words([text("Room 204B"), text("Conference lobby")]) == ["204b", "conference"]

    def test_things_are_judged_by_containment_so_a_partial_view_still_fits(self):
        assert things_score({"door": 2, "chair": 1}, {"door": 2, "chair": 1}) == pytest.approx(1.0)
        assert things_score({"door": 1}, {"cup": 1}) == 0.0
        assert things_score({}, {"door": 1}) is None
        # A step aside shows fewer of the same things: still the same place.
        assert things_score({"door": 2, "cup": 1, "sink": 3}, {"door": 2}) == pytest.approx(1.0)
        # Half of the smaller view is elsewhere: half a fit.
        assert things_score({"door": 2, "chair": 1}, {"door": 1, "table": 1}) == pytest.approx(0.5)

    def test_the_picture_ramp(self):
        assert visual_score(VISUAL_LOW) == 0.0
        assert visual_score(VISUAL_HIGH) == 1.0
        assert visual_score((VISUAL_LOW + VISUAL_HIGH) / 2) == pytest.approx(0.5)
        evidence = compare(scene(HALLWAY, embedding=unit(1, 0)), scene(HALLWAY, embedding=unit(1, 0)))
        assert evidence.score == pytest.approx(1.0)
        assert evidence.describe() == "picture 1.00, things 1.00"


# --- What is and is not remembered ----------------------------------------------------


class TestSubstance:
    def test_an_empty_scan_is_skipped_and_leaves_no_trace(self):
        memory = PlaceMemory()
        result = memory.observe(scene())
        assert result.kind == "skipped" and result.spoken_suffix is None
        assert memory.places == [] and memory.unplaced == []

    def test_a_blank_view_with_only_a_picture_does_not_start_a_place(self):
        memory = PlaceMemory()
        assert memory.observe(scene(embedding=unit(1, 0))).kind == "skipped"
        assert memory.places == []
        # But it can still recognize a place it looks like.
        memory.observe(scene(HALLWAY, embedding=unit(1, 0)))
        assert memory.observe(scene(embedding=unit(1, 0), sid="s2")).kind == "recognized"

    def test_people_and_walls_are_not_part_of_a_place(self):
        seen = [Seen(label, 0.9, 0.0, 2.0, 2, None) for label in ("person", "chair", "wall", "person")]
        built = scene_from_scan(seen, [text("Room 12")], embedding=[0.6, 0.8], thumbnail="data:x")
        assert built.labels == {"chair": 1}
        assert built.words == ["12"]
        assert built.embedding == [0.6, 0.8] and built.thumbnail == "data:x"
        assert built.room is None

    def test_a_scene_keeps_the_layout_the_blueprint_draws(self, tmp_path):
        seen = [
            Seen("chair", 0.9, -12.0, 1.5, 2, None),
            Seen("sign", 0.8, 20.0, None, 2, None),
            Seen("person", 0.9, 5.0, 3.0, 2, None),
            Seen("wall", 0.7, 0.0, None, 2, None),
        ]
        built = scene_from_scan(seen)
        assert built.layout == [
            {"label": "chair", "azimuth_deg": -12.0, "distance_m": 1.5, "obstacle": True, "scale": "large"},
            {"label": "sign", "azimuth_deg": 20.0, "distance_m": None, "obstacle": False, "scale": "medium"},
            {"label": "person", "azimuth_deg": 5.0, "distance_m": 3.0, "obstacle": True, "scale": "large"},
        ]
        memory = PlaceMemory(str(tmp_path / "places.json"))
        memory.remember_new(built)
        reloaded = PlaceMemory(str(tmp_path / "places.json"))
        assert reloaded.places[0].scenes[0].layout == built.layout
        assert reloaded.to_dict()["places"][0]["scenes"][0]["layout"][0]["label"] == "chair"

    def test_a_place_keeps_only_its_newest_views(self):
        memory = PlaceMemory(max_scenes_per_place=3)
        for index in range(5):
            memory.observe(scene(HALLWAY, embedding=unit(1, 0), sid=f"s{index}"), now=float(index))
        assert [s.id for s in memory.places[0].scenes] == ["s2", "s3", "s4"]

    def test_the_current_place_answers_where_am_i_and_then_expires(self):
        memory = PlaceMemory()
        memory.observe(scene(HALLWAY, embedding=unit(1, 0)), now=0.0)
        assert memory.current_name(now=10.0) == "Hallway 1"
        assert memory.current_name(now=1000.0) is None


# --- The helper's edits -------------------------------------------------------


class TestEdits:
    def build(self) -> PlaceMemory:
        memory = PlaceMemory()
        memory.observe(scene(HALLWAY, embedding=unit(1, 0), sid="h1"))
        memory.observe(scene(OFFICE, embedding=unit(0, 1), sid="o1"))
        return memory

    def test_rename(self):
        memory = self.build()
        renamed = memory.rename(memory.places[0].id, "  Main   hall ")
        assert renamed.name == "Main hall"
        assert memory.rename("nope", "x") is None
        assert memory.rename(memory.places[0].id, "   ") is None

    def test_delete_forgets_the_place_and_where_the_user_was(self):
        memory = self.build()
        office = memory.places[1]
        assert memory.current_name(now=1.0) == "Office 1"
        assert memory.delete_place(office.id) is office
        assert [p.name for p in memory.places] == ["Hallway 1"]
        assert memory.current is None
        assert memory.delete_place(office.id) is None

    def test_merge_moves_every_view_and_drops_the_source(self):
        memory = self.build()
        hallway, office = memory.places
        merged = memory.merge(office.id, hallway.id)
        assert merged is hallway
        assert [s.id for s in hallway.scenes] == ["h1", "o1"]
        assert memory.places == [hallway]
        assert memory.current[0] == hallway.id
        assert memory.merge(hallway.id, hallway.id) is None

    def test_an_unplaced_view_can_be_linked_into_a_place_or_start_one(self):
        memory = self.build()
        first, second = at_cosine(VISUAL_LOW + 0.6 * (VISUAL_HIGH - VISUAL_LOW))
        memory.places[0].scenes[0].embedding = first
        unsure = memory.observe(scene(HALLWAY, embedding=second, sid="u1"))
        assert unsure.kind == "unsure"
        assert memory.link("u1", memory.places[0].id).name == "Hallway 1"
        assert memory.unplaced == [] and [s.id for s in memory.places[0].scenes] == ["h1", "u1"]
        memory.observe(scene(HALLWAY, embedding=second, sid="u2"))
        # The linked view makes the next one a recognition; force an unplaced one.
        memory.unplaced.append(scene(["chair"], embedding=unit(0, 0, 1), sid="u3"))
        started = memory.link("u3", None, name="Kitchen corner")
        assert started.name == "Kitchen corner" and [s.id for s in started.scenes] == ["u3"]
        assert memory.link("missing", None) is None

    def test_deleting_the_last_view_removes_the_place(self):
        memory = self.build()
        office = memory.places[1]
        deleted = memory.delete_scene("o1")
        assert deleted[0].id == "o1" and deleted[1] is office
        assert [p.name for p in memory.places] == ["Hallway 1"]
        assert memory.delete_scene("o1") is None

    def test_forget_all(self):
        memory = self.build()
        assert memory.forget_all() == 2
        assert memory.places == [] and memory.current is None


# --- Persistence --------------------------------------------------------------


def test_memory_survives_a_restart(tmp_path):
    path = tmp_path / "places.json"
    memory = PlaceMemory(str(path))
    memory.observe(scene(HALLWAY, words=["204b"], embedding=unit(1, 0)))
    memory.places[0].scenes[0].thumbnail = "data:image/jpeg;base64,AAA"
    memory.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["places"][0]["name"] == "Hallway 1"
    assert saved["places"][0]["scenes"][0]["embedding"] == [1.0, 0.0]

    reloaded = PlaceMemory(str(path))
    assert [p.name for p in reloaded.places] == ["Hallway 1"]
    assert reloaded.places[0].scenes[0].words == ["204b"]
    assert reloaded.observe(scene(HALLWAY, embedding=unit(1, 0), sid="s2")).kind == "recognized"


def test_a_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "places.json"
    path.write_text("{not json", encoding="utf-8")
    assert PlaceMemory(str(path)).places == []


def test_the_panel_view_carries_thumbnails_but_no_fingerprints():
    memory = PlaceMemory()
    memory.observe(scene(HALLWAY, embedding=unit(1, 0)), now=0.0)
    memory.places[0].scenes[0].thumbnail = "data:image/jpeg;base64,AAA"
    view = memory.to_dict(now=5.0)
    assert view["places"][0]["scenes"][0]["thumbnail"] == "data:image/jpeg;base64,AAA"
    assert "embedding" not in view["places"][0]["scenes"][0]
    assert view["current"] == {
        "place_id": memory.places[0].id, "name": "Hallway 1", "score": 1.0, "age_s": 5.0, "fresh": True
    }


# --- Through a scan -----------------------------------------------------------


def detection(label, x1, y1, x2, y2, confidence, azimuth=0.0, distance=2.0) -> Detection:
    return Detection(
        label=label, confidence=confidence, box=BoundingBox(x1, y1, x2, y2),
        azimuth_deg=azimuth, elevation_deg=0.0, distance_m=distance,
    )


class FingerprintDetector(FakeDetector):
    """A detector whose fingerprint of every frame is the same picture."""

    def __init__(self, per_call, vector):
        super().__init__(per_call)
        self.vector = vector
        self.embedded = 0

    async def embed(self, image):
        self.embedded += 1
        return list(self.vector)


@pytest.mark.asyncio
async def test_a_scan_is_remembered_and_the_second_one_is_recognized():
    chair = [detection("chair", 300, 300, 400, 450, 0.7)]
    detector = FingerprintDetector([chair, chair, chair, chair], unit(1, 0))
    socket = FakeSocket()
    memory = PlaceMemory()
    session = Session(socket, FakeProvider(), FakePerception(detector), NullReader(), memory)

    await session.handle_scan([frame(), frame()])
    assert socket.spoken()[-1].endswith("I'll remember this place as Place 1.")
    events = [p for p in socket.sent if p.get("type") == "place"]
    assert events[-1]["kind"] == "new" and events[-1]["place"]["name"] == "Place 1"
    # The scan's inventory says what obstructs, for the blueprint.
    inventory = [p for p in socket.sent if p.get("type") == "inventory"][-1]
    assert inventory["items"][0]["label"] == "chair" and inventory["items"][0]["obstacle"] is True
    assert detector.embedded == 1
    assert memory.places[0].scenes[0].thumbnail.startswith("data:image/jpeg;base64,")

    await session.handle_scan([frame(), frame()])
    assert socket.spoken()[-1].startswith("It looks like you are still in Place 1. ")
    events = [p for p in socket.sent if p.get("type") == "place"]
    assert events[-1]["kind"] == "recognized" and events[-1]["score"] == 1.0


@pytest.mark.asyncio
async def test_a_scan_without_a_memory_is_spoken_as_before():
    chair = [detection("chair", 300, 300, 400, 450, 0.7)]
    socket = FakeSocket()
    session = Session(socket, FakeProvider(), FakePerception(FakeDetector([chair, chair])), NullReader())
    await session.handle_scan([frame(), frame()])
    assert socket.spoken() == [
        "About 2 meters straight ahead, a chair. "
        "The way ahead is blocked by a chair about 2 meters straight ahead."
    ]
    assert not [p for p in socket.sent if p.get("type") == "place"]


@pytest.mark.asyncio
async def test_a_failing_fingerprint_never_reaches_the_spoken_scan():
    class BrokenDetector(FakeDetector):
        async def embed(self, image):
            raise RuntimeError("no clip today")

    chair = [detection("chair", 300, 300, 400, 450, 0.7)]
    socket = FakeSocket()
    session = Session(socket, FakeProvider(), FakePerception(BrokenDetector([chair, chair])), NullReader(), PlaceMemory())
    await session.handle_scan([frame(), frame()])
    assert socket.spoken()[-1].startswith("About 2 meters straight ahead, a chair.")


# --- The panel's endpoints ----------------------------------------------------


def body(response) -> dict:
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_the_panel_endpoints_edit_the_memory():
    memory = PlaceMemory()
    memory.observe(scene(HALLWAY, embedding=unit(1, 0), sid="h1"))
    memory.observe(scene(OFFICE, embedding=unit(0, 1), sid="o1"))
    memory.unplaced.append(scene(["chair"], embedding=unit(0, 0, 1), sid="u1"))
    hallway, office = memory.places
    app.state.places = memory
    try:
        listed = body(await places_index())
        assert [p["name"] for p in listed["places"]] == ["Hallway 1", "Office 1"]
        assert [s["id"] for s in listed["unplaced"]] == ["u1"]

        assert body(await places_rename(hallway.id, PlaceName(name="Main hall")))["places"][0]["name"] == "Main hall"
        assert (await places_rename("nope", PlaceName(name="x"))).status_code == 404

        linked = body(await scenes_link("u1", SceneLink(place_id=office.id)))
        assert linked["unplaced"] == [] and len(linked["places"][1]["scenes"]) == 2
        assert (await scenes_link("gone", SceneLink())).status_code == 404

        merged = body(await places_merge(office.id, PlaceMerge(into=hallway.id)))
        assert [p["name"] for p in merged["places"]] == ["Main hall"]
        assert len(merged["places"][0]["scenes"]) == 3
        assert (await places_merge(office.id, PlaceMerge(into=hallway.id))).status_code == 404

        fewer = body(await scenes_delete("o1"))
        assert len(fewer["places"][0]["scenes"]) == 2
        assert (await scenes_delete("o1")).status_code == 404

        assert body(await places_delete(hallway.id))["places"] == []
        assert (await places_delete(hallway.id)).status_code == 404

        memory.observe(scene(HALLWAY, embedding=unit(1, 0), sid="h2"))
        assert body(await places_forget_all())["places"] == []
    finally:
        app.state.places = None


@pytest.mark.asyncio
async def test_the_panel_without_a_memory_says_so():
    app.state.places = None
    assert body(await places_index()) == {"enabled": False, "places": [], "unplaced": [], "current": None}
    assert (await places_delete("x")).status_code == 404


# --- Questions ----------------------------------------------------------------


def test_where_am_i_answers_with_the_remembered_place_first():
    from backend.ai.local_provider import LocalSceneProvider
    from backend.scene.model import SceneModel

    with_place = LocalSceneProvider(lambda: SceneModel(), place_getter=lambda: "Hallway 1")
    assert with_place._answer("where am I").startswith("You seem to be in Hallway 1. ")
    without = LocalSceneProvider(lambda: SceneModel())
    assert not without._answer("where am I").startswith("You seem")
    forgotten = LocalSceneProvider(lambda: SceneModel(), place_getter=lambda: None)
    assert not forgotten._answer("what room is this").startswith("You seem")


def test_the_online_model_is_told_the_remembered_place():
    from backend.ai.prompts import scene_context

    context = scene_context({"objects": [], "tentative": [], "place": {"name": "Hallway 1", "score": 0.86}})
    assert "REMEMBERED PLACE: the last scan recognized this as Hallway 1 (86% match)" in context
    assert "REMEMBERED PLACE" not in scene_context({"objects": [], "tentative": []})
