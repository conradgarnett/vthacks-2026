"""The eating trigger: food low and near in a first-person view, over
several frames, at the 80% rule; a cue or a food in hand; a window that
forgets."""

from __future__ import annotations

from backend.alerts.eating import EatingDetector, sightings_from
from backend.perception.detector import Detection
from backend.perception.geometry import BoundingBox

FRAME = (1000, 1000)


def det(label: str, confidence: float, y1: float, y2: float, x1: float = 100, x2: float = 300) -> Detection:
    return Detection(label, confidence, BoundingBox(x1, y1, x2, y2), 0.0, 0.0, None)


def feed(detector: EatingDetector, frames: list[list[Detection]], start: float = 0.0, step: float = 0.3) -> float:
    now = start
    for index, detections in enumerate(frames):
        now = start + index * step
        detector.observe(detections, FRAME, now=now)
    return now


def test_a_sandwich_in_hand_over_three_frames_is_eating():
    detector = EatingDetector()
    now = feed(detector, [[det("sandwich", 0.9, 600, 950)]] * 3)
    eating = detector.eating(now=now)
    assert eating.confirmed and eating.in_hand
    assert eating.foods[0].label == "sandwich" and eating.foods[0].frames == 3


def test_one_frame_is_not_eating():
    detector = EatingDetector()
    now = feed(detector, [[det("sandwich", 0.95, 600, 950)]])
    assert not detector.eating(now=now).confirmed


def test_a_pizza_across_the_room_is_not_being_eaten():
    detector = EatingDetector()
    now = feed(detector, [[det("pizza", 0.9, 100, 150)]] * 5)
    eating = detector.eating(now=now)
    assert eating.foods == () and not eating.confirmed


def test_a_bowl_and_spoon_low_in_view_cue_the_food():
    detector = EatingDetector()
    frames = [[det("pizza", 0.85, 700, 850), det("bowl", 0.6, 800, 990)]] * 3
    now = feed(detector, frames)
    eating = detector.eating(now=now)
    assert eating.confirmed and eating.cued and not eating.in_hand
    assert eating.cue_frames == 3


def test_food_without_a_cue_and_not_in_hand_is_not_confirmed():
    detector = EatingDetector()
    now = feed(detector, [[det("pizza", 0.85, 700, 850)]] * 3)
    eating = detector.eating(now=now)
    assert eating.foods and not eating.confirmed


def test_food_below_80_percent_does_not_count():
    detector = EatingDetector()
    now = feed(detector, [[det("pizza", 0.7, 600, 950)]] * 4)
    assert detector.eating(now=now).foods == ()


def test_the_weakest_frame_sets_the_confidence():
    detector = EatingDetector()
    frames = [[det("sandwich", c, 600, 950)] for c in (0.95, 0.85, 0.9)]
    now = feed(detector, frames)
    assert detector.eating(now=now).foods[0].confidence == 0.85


def test_the_window_forgets():
    detector = EatingDetector()
    feed(detector, [[det("sandwich", 0.9, 600, 950)]] * 3)
    assert not detector.eating(now=10.0).confirmed


def test_sightings_normalize_to_the_frame():
    sighting = sightings_from([det("cup", 0.9, 500, 700)], FRAME)[0]
    assert sighting.y_center == 0.6 and abs(sighting.height - 0.2) < 1e-9
    assert sightings_from([det("cup", 0.9, 500, 700)], None) == []


def test_a_chair_is_not_food():
    detector = EatingDetector()
    now = feed(detector, [[det("chair", 0.99, 600, 950)]] * 3)
    assert detector.eating(now=now).foods == ()
