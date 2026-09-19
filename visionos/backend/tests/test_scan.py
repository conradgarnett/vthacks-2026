"""A scan paints the scene, from what two frames agreed on.

Looking down a hallway at a person sitting at a table with two chairs, the
old inventory said "a person about 6 meters away". The picture says where
you are, groups what belongs together, and says what the group is doing.
"""

from __future__ import annotations

import pytest

from backend.main import Session, add_tracked, match_scan_frames
from backend.perception.detector import Detection
from backend.perception.geometry import BoundingBox
from backend.ai.ocr import TextLine
from backend.scene.inference import (
    UNSURE_TALL_BOX,
    Seen,
    describe_group,
    describe_scan,
    group_seen,
    inventory_sentence,
    refine_labels,
)
from backend.scene.model import SceneModel
from backend.tests.test_peek import FakeProvider, FakeSocket, frame


def seen(label, azimuth=0.0, distance=None, confidence=0.9, frames=2, box=None):
    return Seen(label, confidence, azimuth, distance, frames, box)


HALLWAY = [
    seen("hallway", 0.0, None, 0.71),
    seen("person", 2.0, 6.0, 0.91, box=(0.45, 0.40, 0.55, 0.70)),
    seen("chair", 1.0, 6.2, 0.64, box=(0.44, 0.55, 0.56, 0.75)),
    seen("chair", 8.0, 6.1, 0.58, box=(0.58, 0.55, 0.66, 0.74)),
    seen("table", 4.0, 5.8, 0.47, box=(0.40, 0.60, 0.70, 0.72)),
]


class TestPicture:
    def test_the_hallway_scene_is_painted_not_listed(self):
        spoken = describe_scan(SceneModel(), HALLWAY)
        assert spoken.startswith("You're looking down a hallway.")
        assert "a person sitting at a table with 2 chairs, one of them empty" in spoken
        assert "about 6 meters ahead, at the end of the hallway" in spoken.lower()

    def test_things_far_apart_are_separate_groups(self):
        groups = group_seen([seen("door", -60.0, 3.0), seen("chair", 10.0, 1.0), seen("table", 12.0, 1.2)])
        assert sorted(len(g) for g in groups) == [1, 2]

    def test_a_group_without_people_is_counted(self):
        assert describe_group([seen("table", 0, 1.0), seen("chair", 3, 1.1), seen("chair", -3, 1.0)]) == "a table and 2 chairs"

    def test_a_person_next_to_a_chair_is_standing_when_boxes_do_not_overlap(self):
        group = [seen("person", 0, 2.0, box=(0.1, 0.2, 0.2, 0.8)), seen("chair", 5, 2.0, box=(0.6, 0.5, 0.7, 0.8))]
        assert describe_group(group) == "a person standing by a chair, which is empty"

    def test_nothing_seen_says_so(self):
        assert describe_scan(SceneModel(), []) == "I can't make out anything specific right now."

    def test_a_glimpse_is_hedged_and_only_when_sure(self):
        spoken = describe_scan(SceneModel(), [seen("chair", 0, 1.5)], [seen("door", 40.0, 3.0, 0.9, 1)])
        assert "I think there may also be a door slightly to your right." in spoken
        unsure = describe_scan(SceneModel(), [seen("chair", 0, 1.5)], [seen("door", 40.0, 3.0, 0.6, 1)])
        assert "door" not in unsure

    def test_distances_are_rounded_for_speech(self):
        assert "about 1.5 meters" in describe_scan(SceneModel(), [seen("chair", 0, 1.4)]).lower()
        assert "less than a meter" in describe_scan(SceneModel(), [seen("chair", 0, 0.6)]).lower()
        assert "about 6 meters" in describe_scan(SceneModel(), [seen("chair", 0, 6.3)]).lower()

    def test_the_inventory_lists_everything_with_confidence(self):
        text = inventory_sentence([seen("person", 0, 6.0, 0.91)], [seen("bottle", 5, 1.0, 0.35, 1)])
        assert text == "Seen in both frames: person 91% about 6 meters. Seen once: bottle 35% about 1 meters."


def text(line, left, top, height=0.03):
    return TextLine(text=line, confidence=0.9, top=top, left=left, height=height)


class TestActivity:
    """A prediction of what a person is doing, only when the detector was sure
    of both the person and the thing in their hands."""

    DESK = [
        seen("person", 0.0, 2.0, 0.92, box=(0.40, 0.30, 0.60, 0.85)),
        seen("laptop", 2.0, 1.9, 0.88, box=(0.44, 0.55, 0.58, 0.68)),
        seen("desk", 1.0, 2.0, 0.81, box=(0.20, 0.60, 0.80, 0.90)),
        seen("chair", -6.0, 2.1, 0.7, box=(0.15, 0.55, 0.28, 0.85)),
    ]

    def test_a_person_with_a_laptop_at_a_desk_is_using_it(self):
        assert describe_group(self.DESK) == "it looks like a person using a laptop at a desk, with an empty chair"

    def test_the_whole_scan_reads_as_a_picture(self):
        spoken = describe_scan(SceneModel(), self.DESK)
        assert spoken == (
            "This looks like an office. About 2 meters straight ahead, "
            "it looks like a person using a laptop at a desk, with an empty chair."
        )

    def test_an_unsure_laptop_is_only_listed(self):
        group = [replace_conf(self.DESK[0], 0.92), replace_conf(self.DESK[1], 0.6), self.DESK[2]]
        assert describe_group(group) == "a person standing at a desk, and a laptop"

    def test_a_laptop_out_of_reach_is_only_listed(self):
        far = seen("laptop", 15.0, 2.0, 0.9, box=(0.85, 0.55, 0.95, 0.65))
        group = [self.DESK[0], far, self.DESK[2]]
        assert describe_group(group) == "a person standing at a desk, and a laptop"

    def test_a_phone_in_hand_is_a_prediction_too(self):
        group = [seen("person", 0, 1.5, 0.9, box=(0.4, 0.2, 0.6, 0.9)), seen("cell phone", 1.0, 1.4, 0.85, box=(0.5, 0.5, 0.55, 0.58))]
        assert describe_group(group) == "it looks like a person on their phone"


def replace_conf(item, confidence):
    return Seen(item.label, confidence, item.azimuth_deg, item.distance_m, item.frames, item.box)


class TestCues:
    """What is read off an object beats what its outline suggests."""

    def test_a_recycling_symbol_on_a_fridge_makes_it_a_recycling_bin(self):
        fridge = seen("refrigerator", 0, 3.0, 0.9, box=(0.4, 0.3, 0.6, 0.9))
        symbol = seen("recycling symbol", 0, None, 0.6, box=(0.47, 0.5, 0.53, 0.58))
        out = refine_labels([fridge, symbol])
        assert [s.label for s in out] == ["recycling bin"]

    def test_the_word_on_the_bin_relabels_it(self):
        can = seen("trash can", 0, 2.0, 0.7, box=(0.4, 0.3, 0.6, 0.9))
        assert refine_labels([can], [text("RECYCLE", 0.45, 0.5)])[0].label == "recycling bin"
        assert refine_labels([can], [text("COMPOST ONLY", 0.45, 0.5)])[0].label == "compost bin"
        fridge = seen("refrigerator", 0, 2.0, 0.9, box=(0.4, 0.3, 0.6, 0.9))
        assert refine_labels([fridge], [text("LANDFILL", 0.45, 0.5)])[0].label == "trash can"

    def test_words_elsewhere_in_the_frame_do_not_relabel(self):
        can = seen("trash can", 0, 2.0, 0.7, box=(0.4, 0.3, 0.6, 0.9))
        assert refine_labels([can], [text("RECYCLE", 0.9, 0.1)])[0].label == "trash can"

    def test_an_unsure_fridge_with_no_kitchen_around_it_is_hedged(self):
        fridge = seen("refrigerator", 0, 3.0, 0.55, box=(0.4, 0.3, 0.6, 0.9))
        assert refine_labels([fridge])[0].label == UNSURE_TALL_BOX
        assert "a large cabinet or bin" in describe_scan(SceneModel(), refine_labels([fridge]))

    def test_a_fridge_beside_a_sink_stays_a_fridge(self):
        fridge = seen("refrigerator", 0, 3.0, 0.55, box=(0.4, 0.3, 0.6, 0.9))
        assert refine_labels([fridge, seen("sink", 20, 3.0)])[0].label == "refrigerator"

    def test_a_sure_fridge_stays_a_fridge(self):
        fridge = seen("refrigerator", 0, 3.0, 0.9, box=(0.4, 0.3, 0.6, 0.9))
        assert refine_labels([fridge])[0].label == "refrigerator"

    def test_a_lone_symbol_means_a_bin_by_direction_only(self):
        symbol = seen("recycling symbol", 30.0, None, 0.7, box=(0.7, 0.5, 0.74, 0.56))
        out = refine_labels([symbol])
        assert [s.label for s in out] == ["recycling bin"] and out[0].distance_m is None
        assert "recycling symbol" not in describe_scan(SceneModel(), out)


def detection(label, x1, y1, x2, y2, confidence=0.8, azimuth=0.0, distance=2.0):
    return Detection(label, confidence, BoundingBox(x1, y1, x2, y2), azimuth, 0.0, distance)


class TestMatching:
    def test_a_thing_in_both_frames_counts_and_one_frame_does_not(self):
        first = [detection("chair", 100, 100, 200, 220), detection("bottle", 500, 500, 540, 600, 0.4)]
        second = [detection("chair", 104, 98, 206, 224, 0.7)]
        both, once = match_scan_frames([first, second], (1280, 720))
        assert [s.label for s in both] == ["chair"]
        assert both[0].confidence == 0.8 and both[0].frames == 2
        assert [s.label for s in once] == ["bottle"]

    def test_one_frame_confirms_nothing(self):
        both, once = match_scan_frames([[detection("chair", 0, 0, 10, 10)]], (100, 100))
        assert both == [] and len(once) == 1

    def test_tracked_objects_join_unless_already_seen_there(self):
        class Tracked:
            def __init__(self, label, azimuth, visible=True):
                self.label, self.azimuth_deg, self.visible = label, azimuth, visible
                self.confidence, self.distance_m = 0.85, 2.0

        out = add_tracked([seen("chair", 0.0, 2.0)], [Tracked("chair", 5.0), Tracked("door", 40.0), Tracked("table", 0.0, visible=False)])
        assert sorted(s.label for s in out) == ["chair", "door"]


class FakeDetector:
    def __init__(self, per_call):
        self.per_call = list(per_call)
        self.sizes = []

    async def detect(self, image, imgsz=None):
        self.sizes.append(imgsz)
        return self.per_call.pop(0) if self.per_call else []


class FakePerception:
    def __init__(self, detector):
        self.detector = detector
        self.scene = SceneModel()


class NullReader:
    name = "none"
    available = False


@pytest.mark.asyncio
async def test_a_scan_burst_is_detected_at_scan_size_and_painted():
    chair = [detection("chair", 300, 300, 400, 450, 0.7, azimuth=0.0, distance=2.0)]
    detector = FakeDetector([chair, chair])
    socket = FakeSocket()
    session = Session(socket, FakeProvider(), FakePerception(detector), NullReader())
    await session.handle_scan([frame(), frame()])
    assert detector.sizes == [1280, 1280]
    assert socket.spoken() == ["About 2 meters straight ahead, a chair."]
    inventory = [p for p in socket.sent if p.get("type") == "inventory"]
    assert inventory and inventory[0]["items"][0]["label"] == "chair" and inventory[0]["items"][0]["frames"] == 2
