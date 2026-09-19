"""Tracker + scene model + queries.

The behaviors under test are the ones that separate a scene model from a
frame-by-frame detector: stable identity, memory after an object leaves view,
confidence that decays, and path clearance that respects distance.
"""

from __future__ import annotations

import time

import pytest

from backend.perception.detector import Detection
from backend.perception.geometry import BoundingBox
from backend.perception.tracker import Tracker
from backend.scene.model import SceneModel
from backend.scene.queries import (
    check_path_clearance,
    find_object,
    nearest_objects,
    summarize,
    what_changed,
)


def make_detection(
    label: str = "chair",
    x: float = 300,
    distance: float | None = 2.0,
    azimuth: float = 0.0,
    confidence: float = 0.9,
) -> Detection:
    return Detection(
        label=label,
        confidence=confidence,
        box=BoundingBox(x, 100, x + 80, 300),
        azimuth_deg=azimuth,
        elevation_deg=0.0,
        distance_m=distance,
    )


def build_scene(detections: list[Detection], frames: int = 4) -> SceneModel:
    """Run detections through the tracker enough times to confirm them.

    Four frames by default so objects clear the hazard engine's persistence
    bar as well as the tracker's -- alerts require more evidence than mere
    presence does.
    """
    tracker, scene = Tracker(), SceneModel()
    for _ in range(frames):
        tracker.update(detections)
    scene.update(tracker.visible(), tracker.remembered())
    return scene


class TestTracker:
    def test_same_object_keeps_its_id_across_frames(self):
        tracker = Tracker()
        tracker.update([make_detection(x=300)])
        tracker.update([make_detection(x=308)])  # small motion
        assert len(tracker.tracks) == 1

    def test_large_jump_is_treated_as_a_new_object(self):
        tracker = Tracker()
        tracker.update([make_detection(x=0)])
        tracker.update([make_detection(x=500)])
        assert len(tracker.tracks) == 2

    def test_different_labels_never_associate(self):
        """A person must never inherit a chair's track."""
        tracker = Tracker()
        tracker.update([make_detection(label="chair", x=300)])
        tracker.update([make_detection(label="person", x=300)])
        assert len(tracker.tracks) == 2

    def test_single_frame_detection_is_not_reported(self):
        """Suppresses false positives that would otherwise be spoken aloud."""
        tracker = Tracker()
        tracker.update([make_detection()])
        assert tracker.visible() == []

    def test_object_is_remembered_after_leaving_view(self):
        tracker = Tracker()
        tracker.update([make_detection()])
        tracker.update([make_detection()])
        assert len(tracker.visible()) == 1

        tracker.update([])  # gone from frame
        assert tracker.visible() == []
        assert len(tracker.remembered()) == 1, "object permanence lost"

    def test_memory_expires_eventually(self):
        tracker = Tracker(max_unseen_s=0.05)
        tracker.update([make_detection()])
        tracker.update([make_detection()])
        time.sleep(0.08)
        tracker.update([])
        assert tracker.remembered() == []


class TestSceneModel:
    def test_visible_object_appears_in_snapshot(self):
        scene = build_scene([make_detection(label="chair")])
        snapshot = scene.snapshot()
        assert snapshot["object_count"] == 1
        assert snapshot["objects"][0]["label"] == "chair"
        assert snapshot["objects"][0]["visible"] is True

    def test_confidence_decays_once_out_of_view(self):
        tracker, scene = Tracker(), SceneModel(memory_s=10.0)
        tracker.update([make_detection(confidence=0.9)])
        tracker.update([make_detection(confidence=0.9)])
        scene.update(tracker.visible(), tracker.remembered())
        fresh = scene.all_objects()[0].decayed_confidence(10.0)

        tracker.update([])
        scene.update(tracker.visible(), tracker.remembered())
        obj = scene.all_objects()[0]
        obj.last_seen = time.monotonic() - 5.0  # simulate 5s out of view

        assert obj.decayed_confidence(10.0) < fresh
        assert obj.decayed_confidence(10.0) == pytest.approx(0.45, abs=0.05)

    def test_remembered_object_keeps_its_last_known_position(self):
        """Position must not drift once we can no longer see it."""
        tracker, scene = Tracker(), SceneModel()
        tracker.update([make_detection(azimuth=30.0)])
        tracker.update([make_detection(azimuth=30.0)])
        scene.update(tracker.visible(), tracker.remembered())

        tracker.update([])
        scene.update(tracker.visible(), tracker.remembered())
        obj = scene.all_objects()[0]

        assert obj.visible is False
        assert obj.azimuth_deg == pytest.approx(30.0)

    def test_objects_sort_nearest_first(self):
        scene = build_scene(
            [
                make_detection(label="couch", x=100, distance=5.0),
                make_detection(label="chair", x=400, distance=1.0),
            ]
        )
        assert [o.label for o in scene.all_objects()] == ["chair", "couch"]

    def test_snapshot_is_capped_to_bound_prompt_cost(self):
        detections = [
            make_detection(label=f"chair", x=i * 60, distance=float(i + 1))
            for i in range(20)
        ]
        scene = build_scene(detections)
        assert len(scene.snapshot(limit=12)["objects"]) == 12

    def test_what_changed_reports_new_arrivals(self):
        scene = build_scene([make_detection(label="person")])
        changed = what_changed(scene, seconds=30.0)
        assert [c["label"] for c in changed] == ["person"]


class TestQueries:
    def test_find_matches_common_synonyms(self):
        scene = build_scene([make_detection(label="couch")])
        assert find_object(scene, "sofa"), "expected 'sofa' to resolve to couch"

    def test_find_is_case_insensitive(self):
        scene = build_scene([make_detection(label="chair")])
        assert find_object(scene, "CHAIR")

    def test_nearest_skips_objects_without_a_distance(self):
        scene = build_scene(
            [
                make_detection(label="chair", x=100, distance=None),
                make_detection(label="person", x=400, distance=2.0),
            ]
        )
        assert [o.label for o in nearest_objects(scene, 3)] == ["person"]

    def test_path_blocked_by_obstacle_dead_ahead(self):
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=1.5)])
        result = check_path_clearance(scene, width_m=0.8, distance_m=3.0)
        assert result["clear"] is False
        assert result["nearest_blocker"] == "chair"

    def test_same_angle_is_clear_when_far_enough_to_the_side(self):
        """The distance-dependence a fixed cone would get wrong."""
        near = build_scene([make_detection(label="chair", azimuth=15.0, distance=1.0)])
        far = build_scene([make_detection(label="chair", azimuth=15.0, distance=5.0)])

        assert check_path_clearance(near, width_m=0.8)["clear"] is False
        assert check_path_clearance(far, width_m=0.8, distance_m=8.0)["clear"] is True

    def test_pedestrian_slightly_off_centre_is_reported(self):
        """Regression: a real photo put a person 2.35 m ahead at 10 deg --
        0.41 m off-centre -- and an 0.8 m corridor called the path clear."""
        scene = build_scene(
            [make_detection(label="person", azimuth=10.0, distance=2.35)]
        )
        assert check_path_clearance(scene)["clear"] is False

    def test_non_obstacle_does_not_block_the_path(self):
        """A cup on a table is not something you walk into."""
        scene = build_scene([make_detection(label="cup", azimuth=0.0, distance=1.0)])
        assert check_path_clearance(scene)["clear"] is True

    def test_empty_scene_is_clear(self):
        assert check_path_clearance(SceneModel())["clear"] is True

    def test_summarize_is_speakable_and_mentions_distance(self):
        scene = build_scene([make_detection(label="chair", distance=2.0)])
        spoken = summarize(scene)
        assert "chair" in spoken and "meters" in spoken
        # Spoken aloud: no markup should ever reach the TTS engine.
        assert not any(ch in spoken for ch in "*_#[]{}")

    def test_summarize_handles_an_empty_scene_gracefully(self):
        assert "can't" in summarize(SceneModel())
