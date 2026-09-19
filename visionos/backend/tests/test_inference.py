"""Predictions from incomplete evidence.

The property under test is honesty of wording: a guess is always spoken as a
guess. A room named from two objects says "may be"; a glimpsed object says
"I think"; a landmark that left the frame is in the past tense.
"""

from __future__ import annotations

import time

import pytest

from backend.ai.local_provider import LocalSceneProvider
from backend.perception.tracker import Tracker
from backend.scene.inference import (
    describe_scene,
    remembered_landmark,
    room_guess,
    room_sentence,
    snapshot_extras,
    tentative_objects,
)
from backend.scene.model import SceneModel
from backend.tests.test_scene import build_scene, make_detection


class TestRoomGuess:
    def test_fridge_and_sink_make_a_kitchen(self):
        assert room_guess(["refrigerator", "sink"])[0] == "kitchen"

    def test_a_bed_alone_is_enough(self):
        assert room_guess(["bed"])[0] == "bedroom"

    def test_a_chair_alone_settles_nothing(self):
        """Chairs are everywhere; one implies no room at all."""
        assert room_guess(["chair"]) is None

    def test_desk_laptop_and_chair_are_an_office_not_a_dining_area(self):
        assert room_guess(["desk", "laptop", "chair"])[0] == "office"

    def test_a_fifth_chair_adds_no_evidence(self):
        assert room_guess(["chair"] * 5) is None

    def test_strong_evidence_says_looks_like_weak_says_may_be(self):
        assert room_sentence(["refrigerator", "sink"]) == "This looks like a kitchen."
        assert room_sentence(["door", "handrail"]) == "This may be a corridor."

    def test_article_is_right_for_an_office(self):
        assert room_sentence(["desk", "laptop"]) == "This looks like an office."


class TestTentative:
    def test_unconfirmed_detection_is_offered_confirmed_one_is_not(self):
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=2.0)])
        latest = [
            make_detection(label="chair", azimuth=0.0, distance=2.0),
            make_detection(label="door", x=500, azimuth=40.0, distance=3.0, confidence=0.4),
        ]
        assert [t.label for t in tentative_objects(latest, scene)] == ["door"]

    def test_navigation_objects_outrank_tabletop_ones(self):
        scene = SceneModel()
        latest = [
            make_detection(label="cup", x=100, azimuth=-10.0, distance=1.0, confidence=0.9),
            make_detection(label="door", x=500, azimuth=40.0, distance=3.0, confidence=0.4),
        ]
        assert [t.label for t in tentative_objects(latest, scene)][0] == "door"

    def test_noise_below_the_floor_is_not_offered(self):
        latest = [make_detection(label="door", azimuth=0.0, distance=3.0, confidence=0.1)]
        assert tentative_objects(latest, SceneModel()) == []

    def test_limit_is_respected(self):
        latest = [
            make_detection(label=label, x=i * 100, azimuth=float(i * 10), distance=2.0)
            for i, label in enumerate(["door", "stairs", "chair", "table"])
        ]
        assert len(tentative_objects(latest, SceneModel(), limit=2)) == 2


class TestDescribeScene:
    def test_room_comes_first_then_inventory_then_guess(self):
        scene = build_scene(
            [
                make_detection(label="refrigerator", x=100, azimuth=-20.0, distance=2.5),
                make_detection(label="sink", x=400, azimuth=10.0, distance=2.0),
            ]
        )
        latest = [make_detection(label="door", x=600, azimuth=45.0, distance=3.0, confidence=0.4)]
        spoken = describe_scene(scene, latest)
        assert spoken.startswith("This looks like a kitchen.")
        assert "refrigerator" in spoken and "sink" in spoken
        assert "I think there may also be a door at your" in spoken

    def test_nothing_confirmed_is_still_hedged_not_silent(self):
        latest = [make_detection(label="door", azimuth=30.0, distance=3.0, confidence=0.4)]
        spoken = describe_scene(SceneModel(), latest)
        assert spoken.startswith("I can't confirm anything yet, but I think there may be a door")

    def test_nothing_at_all_says_so(self):
        assert "can't" in describe_scene(SceneModel(), [])

    def test_a_guess_never_reads_as_a_fact(self):
        latest = [make_detection(label="stairs", azimuth=0.0, distance=None, confidence=0.5)]
        spoken = describe_scene(SceneModel(), latest)
        assert "I think" in spoken
        assert "There are stairs" not in spoken and "There's stairs" not in spoken

    def test_output_is_speakable(self):
        scene = build_scene([make_detection(label="bed", distance=2.0)])
        spoken = describe_scene(scene, [])
        assert not any(ch in spoken for ch in "*_#[]{}<>")


class TestReminder:
    def test_landmark_that_just_left_view_is_reported_in_the_past_tense(self):
        tracker, scene = Tracker(), SceneModel()
        for _ in range(3):
            tracker.update([make_detection(label="door", azimuth=60.0, distance=3.0)])
        tracker.update([])  # leaves the frame
        scene.update(tracker.visible(), tracker.remembered())
        scene.all_objects()[0].last_seen = time.monotonic() - 2.0

        assert remembered_landmark(scene) is not None
        assert "The door was at your two o'clock a moment ago." in describe_scene(scene, [])

    def test_an_object_still_in_view_is_not_a_reminder(self):
        scene = build_scene([make_detection(label="door", azimuth=60.0, distance=3.0)])
        assert remembered_landmark(scene) is None

    def test_a_chair_is_not_worth_a_reminder(self):
        tracker, scene = Tracker(), SceneModel()
        for _ in range(3):
            tracker.update([make_detection(label="chair", azimuth=60.0, distance=3.0)])
        tracker.update([])
        scene.update(tracker.visible(), tracker.remembered())
        scene.all_objects()[0].last_seen = time.monotonic() - 2.0
        assert remembered_landmark(scene) is None


class TestSnapshotExtras:
    def test_room_and_tentative_travel_with_the_snapshot(self):
        scene = build_scene([make_detection(label="bed", distance=2.0)])
        latest = [make_detection(label="door", x=500, azimuth=40.0, distance=3.0, confidence=0.4)]
        extras = snapshot_extras(scene, latest)
        assert extras["room"] == "bedroom"
        assert extras["tentative"][0]["label"] == "door"


async def answer(scene: SceneModel, prompt: str, latest=(), intent: str | None = None) -> str:
    provider = LocalSceneProvider(lambda: scene, lambda: list(latest))
    chunks = [c async for c in provider.describe(b"", prompt, intent=intent)]
    return "".join(chunks).strip()


@pytest.mark.asyncio
async def test_scan_reasons_past_the_confirmed_list():
    scene = build_scene(
        [
            make_detection(label="refrigerator", x=100, azimuth=-20.0, distance=2.5),
            make_detection(label="sink", x=400, azimuth=10.0, distance=2.0),
        ]
    )
    latest = [make_detection(label="door", x=600, azimuth=45.0, distance=3.0, confidence=0.4)]
    spoken = await answer(scene, "scan", latest, intent="scan")
    assert spoken.startswith("This looks like a kitchen.")
    assert "I think there may also be a door" in spoken


@pytest.mark.asyncio
async def test_what_room_question_gets_the_room():
    scene = build_scene([make_detection(label="bed", distance=2.0)])
    assert (await answer(scene, "What room is this?")) == "This looks like a bedroom."


@pytest.mark.asyncio
async def test_what_room_question_admits_not_knowing():
    scene = build_scene([make_detection(label="chair", distance=2.0)])
    spoken = await answer(scene, "Where am I?")
    assert "can't tell" in spoken.lower()


@pytest.mark.asyncio
async def test_describe_this_room_is_still_an_inventory_not_a_room_question():
    """The word "room" alone must not hijack a description request."""
    scene = build_scene([make_detection(label="chair", distance=2.0)])
    spoken = await answer(scene, "Describe this room")
    assert "chair" in spoken and "meters" in spoken
