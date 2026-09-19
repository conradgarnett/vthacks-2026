"""Hazard rules.

This is the one subsystem that must work when everything else is broken, so
it is deterministic and tested directly. Two failure modes matter equally:
missing a real obstacle, and crying wolf until the user switches the tool off.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from backend.hazards.engine import HazardEngine, Severity
from backend.tests.test_scene import build_scene, make_detection


@dataclass
class FakeDropoff:
    azimuth_deg: float
    severity: float


@pytest.fixture
def engine() -> HazardEngine:
    return HazardEngine(distance_m=1.5, cone_deg=30.0, cooldown_s=3.0)


class TestObstacleDetection:
    def test_obstacle_in_the_path_raises_an_alert(self, engine):
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=1.0)])
        alerts = engine.evaluate(scene)
        assert len(alerts) == 1
        assert "chair" in alerts[0].text.lower()

    def test_obstacle_outside_the_cone_is_ignored(self, engine):
        """Something well off to the side is not in the walking path."""
        scene = build_scene([make_detection(label="chair", azimuth=60.0, distance=1.0)])
        assert engine.evaluate(scene) == []

    def test_distant_obstacle_is_ignored(self, engine):
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=5.0)])
        assert engine.evaluate(scene) == []

    def test_non_obstacle_never_alerts(self, engine):
        """A cup on a table is not something you walk into."""
        scene = build_scene([make_detection(label="cup", azimuth=0.0, distance=0.8)])
        assert engine.evaluate(scene) == []

    def test_empty_scene_is_quiet(self, engine):
        from backend.scene.model import SceneModel

        assert engine.evaluate(SceneModel()) == []


class TestCredibility:
    """Alerts demand more evidence than descriptions.

    Reported from real use: open-vocabulary detection flickers, and every
    one-frame ghost inside the cone became a spoken warning.
    """

    def test_a_flickering_one_frame_detection_does_not_alert(self, engine):
        scene = build_scene(
            [make_detection(label="chair", azimuth=0.0, distance=1.0)], frames=1
        )
        assert engine.evaluate(scene) == []

    def test_a_persistent_detection_does_alert(self, engine):
        scene = build_scene(
            [make_detection(label="chair", azimuth=0.0, distance=1.0)], frames=4
        )
        assert len(engine.evaluate(scene)) == 1

    def test_a_low_confidence_detection_does_not_alert(self, engine):
        """Good enough to mention; not good enough to interrupt for."""
        scene = build_scene(
            [
                make_detection(
                    label="chair", azimuth=0.0, distance=1.0, confidence=0.22
                )
            ],
            frames=4,
        )
        assert engine.evaluate(scene) == []

    def test_a_remembered_object_does_not_alert(self, engine):
        """Warning about something we can no longer see is a false alarm."""
        from backend.perception.tracker import Tracker
        from backend.scene.model import SceneModel

        tracker, scene = Tracker(), SceneModel()
        detection = make_detection(label="chair", azimuth=0.0, distance=1.0)
        for _ in range(4):
            tracker.update([detection])
        tracker.update([])  # leaves view
        scene.update(tracker.visible(), tracker.remembered())

        assert engine.evaluate(scene) == []

    def test_the_threshold_is_configurable(self):
        scene = build_scene(
            [make_detection(label="chair", azimuth=0.0, distance=1.0)], frames=2
        )
        assert HazardEngine(min_hits=10).evaluate(scene) == []
        assert len(HazardEngine(min_hits=1).evaluate(scene)) == 1


class TestSeverity:
    def test_very_close_obstacle_is_urgent_and_uses_steps(self, engine):
        """At arm's length, 'two steps' beats a decimal measurement."""
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=0.9)])
        alert = engine.evaluate(scene)[0]
        assert alert.severity == Severity.URGENT
        assert "step" in alert.text

    def test_further_obstacle_is_a_warning_with_a_clock_position(self, engine):
        scene = build_scene([make_detection(label="chair", azimuth=10.0, distance=1.4)])
        alert = engine.evaluate(scene)[0]
        assert alert.severity == Severity.WARNING
        assert "o'clock" in alert.text

    def test_step_is_singular_when_there_is_one(self, engine):
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=0.6)])
        assert "1 step." in engine.evaluate(scene)[0].text


class TestDebouncing:
    def test_same_obstacle_does_not_repeat_within_the_cooldown(self, engine):
        """A tool that repeats four times a second gets switched off."""
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=1.0)])
        assert len(engine.evaluate(scene)) == 1
        assert engine.evaluate(scene) == []
        assert engine.evaluate(scene) == []

    def test_same_obstacle_repeats_after_the_cooldown(self):
        engine = HazardEngine(cooldown_s=0.05)
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=1.0)])
        assert len(engine.evaluate(scene)) == 1
        time.sleep(0.08)
        assert len(engine.evaluate(scene)) == 1

    def test_a_different_object_alerts_immediately(self, engine):
        """Debouncing is per object, not global -- a new hazard is new."""
        chair = build_scene([make_detection(label="chair", azimuth=0.0, distance=1.0)])
        assert len(engine.evaluate(chair)) == 1

        person = build_scene([make_detection(label="person", azimuth=0.0, distance=1.0)])
        assert len(engine.evaluate(person)) == 1

    def test_only_one_hazard_is_spoken_per_frame(self, engine):
        """Queued alerts arrive after the moment they described has passed."""
        scene = build_scene(
            [
                make_detection(label="chair", x=100, azimuth=-5.0, distance=1.0),
                make_detection(label="person", x=300, azimuth=5.0, distance=0.8),
            ]
        )
        assert len(engine.evaluate(scene)) == 1

    def test_the_most_urgent_hazard_wins(self, engine):
        scene = build_scene(
            [
                make_detection(label="chair", x=100, azimuth=-5.0, distance=1.45),
                make_detection(label="person", x=300, azimuth=5.0, distance=0.7),
            ]
        )
        assert "person" in engine.evaluate(scene)[0].text.lower()


class TestDropoffs:
    def test_confident_dropoff_alerts_urgently(self, engine):
        from backend.scene.model import SceneModel

        alerts = engine.evaluate(SceneModel(), [FakeDropoff(0.0, 0.9)])
        assert alerts and alerts[0].severity == Severity.URGENT

    def test_dropoff_wording_is_hedged(self, engine):
        """This is a depth discontinuity, not a verified staircase. Claiming
        'stairs ahead' would be asserting something we cannot know."""
        from backend.scene.model import SceneModel

        text = engine.evaluate(SceneModel(), [FakeDropoff(0.0, 0.9)])[0].text.lower()
        assert "may" in text
        assert "stair" not in text

    def test_weak_dropoff_signal_is_ignored(self, engine):
        from backend.scene.model import SceneModel

        assert engine.evaluate(SceneModel(), [FakeDropoff(0.0, 0.2)]) == []

    def test_dropoff_outranks_an_obstacle(self, engine):
        """The floor falling away beats anything standing on it."""
        scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=1.0)])
        alerts = engine.evaluate(scene, [FakeDropoff(0.0, 0.9)])
        assert "floor" in alerts[0].text.lower()
