"""A scan paints the scene, from what two frames agreed on.

Looking down a hallway at a person sitting at a table with two chairs, the
old inventory said "a person about 6 meters away". The picture says where
you are, groups what belongs together, and says what the group is doing.
"""

from __future__ import annotations

import pytest

from backend.main import Session, add_tracked, detection_items, match_scan_frames
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
    notable_text,
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
        assert describe_scan(SceneModel(), []) == (
            "I can't make out anything specific right now. I can't tell whether the way ahead is clear."
        )

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
        assert text == "Seen in both frames: person 91% about 6 meters. Seen once: bottle 35% about 1 meter."


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
            "it looks like a person using a laptop at a desk, with an empty chair. "
            "The way ahead is blocked by a person about 2 meters straight ahead."
        )

    def test_an_unsure_laptop_is_only_listed(self):
        group = [replace_conf(self.DESK[0], 0.92), replace_conf(self.DESK[1], 0.6), self.DESK[2]]
        # The desk is sure, so the place prediction stands in for the object one.
        assert describe_group(group) == "it looks like a person working at a desk, and a laptop"

    def test_a_laptop_out_of_reach_is_only_listed(self):
        far = seen("laptop", 15.0, 2.0, 0.9, box=(0.85, 0.55, 0.95, 0.65))
        group = [self.DESK[0], far, self.DESK[2]]
        # The desk is sure, so the place prediction stands in for the object one.
        assert describe_group(group) == "it looks like a person working at a desk, and a laptop"

    def test_a_phone_in_hand_is_a_prediction_too(self):
        group = [seen("person", 0, 1.5, 0.9, box=(0.4, 0.2, 0.6, 0.9)), seen("cell phone", 1.0, 1.4, 0.85, box=(0.5, 0.5, 0.55, 0.58))]
        assert describe_group(group) == "it looks like a person on their phone"


class TestPlaceActivity:
    """What a person is doing, read from where they are when nothing is in hand."""

    def test_a_person_at_a_desk_is_working(self):
        group = [
            seen("person", 0.0, 2.0, 0.9, box=(0.40, 0.30, 0.60, 0.85)),
            seen("desk", 1.0, 2.0, 0.85, box=(0.20, 0.60, 0.80, 0.90)),
            seen("chair", -6.0, 2.1, 0.7, box=(0.15, 0.55, 0.28, 0.85)),
        ]
        assert describe_group(group) == "it looks like a person working at a desk, with an empty chair"

    def test_an_unsure_desk_is_only_a_desk(self):
        group = [seen("person", 0.0, 2.0, 0.9, box=(0.40, 0.30, 0.60, 0.85)), seen("desk", 1.0, 2.0, 0.6, box=(0.20, 0.60, 0.80, 0.90))]
        assert describe_group(group) == "a person standing at a desk"

    def test_a_person_on_a_couch(self):
        group = [seen("person", 0.0, 2.0, 0.9, box=(0.40, 0.30, 0.60, 0.80)), seen("couch", 0.0, 2.0, 0.88, box=(0.20, 0.55, 0.80, 0.95))]
        assert describe_group(group) == "it looks like a person sitting on a couch"


class TestNotableText:
    def test_an_exit_sign_is_read_out_with_its_direction(self):
        assert notable_text([text("EXIT", 0.1, 0.2)], []) == ["A sign to your left says EXIT."]

    def test_text_on_a_door_is_read_out_whatever_it_says(self):
        door = seen("door", 0.0, 3.0, 0.9, box=(0.4, 0.1, 0.6, 0.9))
        assert notable_text([text("STAFF ONLY", 0.45, 0.4)], [door]) == ["The door ahead says STAFF ONLY."]

    def test_ordinary_text_is_left_for_a_read(self):
        assert notable_text([text("LISINOPRIL 10 MG", 0.45, 0.4), text("Diet Cola", 0.7, 0.5)], []) == []

    def test_at_most_two_nearest_the_middle_and_no_repeats(self):
        lines = [text("EXIT", 0.05, 0.2), text("EXIT", 0.06, 0.6), text("STAIRS", 0.45, 0.3), text("PUSH", 0.9, 0.5)]
        out = notable_text(lines, [])
        assert len(out) == 2 and out[0] == "A sign ahead says STAIRS."

    def test_the_scan_speaks_the_sign_after_the_picture(self):
        spoken = describe_scan(SceneModel(), [seen("chair", 0, 1.5)], [], [text("FIRE EXIT", 0.8, 0.2)])
        assert "A sign to your right says FIRE EXIT." in spoken
        # The walkway comes last, every time.
        assert spoken.endswith("The way ahead is blocked by a chair about 1.5 meters straight ahead."), spoken


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
    assert socket.spoken() == [
        "About 2 meters straight ahead, a chair. "
        "The way ahead is blocked by a chair about 2 meters straight ahead."
    ]
    inventory = [p for p in socket.sent if p.get("type") == "inventory"]
    assert inventory and inventory[0]["items"][0]["label"] == "chair" and inventory[0]["items"][0]["frames"] == 2


def test_detections_are_sent_with_boxes_normalized_to_the_frame():
    items = detection_items([detection("door", 64, 72, 320, 648, 0.84)], (640, 720))
    assert items == [{"label": "door", "confidence": 0.84, "scale": "large", "box": [0.1, 0.1, 0.5, 0.9]}]
    assert detection_items([detection("door", 0, 0, 10, 10)], None) == []


# --- Hand-held things are for questions, not scans ---------------------------


def test_hand_held_things_are_left_out_of_a_scan():
    """The user's rule: people, chairs, tables and laptops are spoken; a
    phone, a bottle or a cup on the table only when asked about."""
    spoken = describe_scan(SceneModel(), [
        seen("table", 0.0, 2.0), seen("chair", 5.0, 2.2), seen("laptop", 2.0, 2.0),
        seen("cup", 1.0, 2.0), seen("bottle", -2.0, 2.0), seen("cell phone", 3.0, 2.0),
    ])
    assert "table" in spoken and "chair" in spoken and "laptop" in spoken, spoken
    for small in ("cup", "bottle", "phone"):
        assert small not in spoken, spoken


def test_only_hand_held_things_in_view_is_nothing_specific():
    spoken = describe_scan(SceneModel(), [seen("cup", 0.0, 1.0), seen("remote", 3.0, 1.0)])
    assert "can't make out anything specific" in spoken, spoken
    assert "cup" not in spoken and "remote" not in spoken


def test_a_phone_in_hand_still_says_what_the_person_is_doing():
    person = seen("person", 0.0, 2.0, box=(0.4, 0.2, 0.6, 0.9))
    phone = seen("cell phone", 0.0, 2.0, box=(0.48, 0.5, 0.52, 0.56))
    spoken = describe_scan(SceneModel(), [person, phone])
    assert "on their phone" in spoken, spoken
    beside_a_chair = describe_scan(SceneModel(), [seen("chair", 0.0, 2.0), phone])
    assert "phone" not in beside_a_chair, beside_a_chair


def test_glimpsed_hand_held_things_are_not_hedged():
    spoken = describe_scan(
        SceneModel(), [seen("chair", 0.0, 2.0)],
        glimpsed=[seen("cell phone", 10.0, 1.5, confidence=0.95, frames=1)],
    )
    assert "phone" not in spoken and "may also be" not in spoken, spoken


def test_detections_carry_their_size_tier_for_the_overlay():
    items = detection_items(
        [detection("cup", 0, 0, 40, 40), detection("laptop", 0, 0, 90, 60), detection("chair", 0, 0, 200, 300)],
        (640, 480),
    )
    assert [i["scale"] for i in items] == ["small", "medium", "large"]


def test_size_tiers_follow_the_height_prior_unless_the_entry_says_otherwise():
    from backend.perception.vocabulary import SMALL_CLASSES, scale_of

    assert {"cup", "bottle", "bowl", "cell phone", "remote", "book", "keyboard"} <= SMALL_CLASSES
    assert scale_of("laptop") == "medium" and scale_of("sink") == "medium"
    assert scale_of("exit sign") == "medium", "a wayfinding sign is small but never left out"
    assert scale_of("person") == "large" and scale_of("chair") == "large"
    assert scale_of("stairs") == "large", "no height prior means spoken"
    assert scale_of("large cabinet or bin") == "large", "a refined label is spoken"


# --- A wider vocabulary, still tiered and still guarded -----------------------


def test_the_vocabulary_grew_with_priors_and_tiers():
    from backend.perception.vocabulary import CLASS_NAMES, HIGH_PRECISION_CLASSES, VOCABULARY, scale_of

    assert len(CLASS_NAMES) >= 110 and len(set(CLASS_NAMES)) == len(CLASS_NAMES)
    for label in ("armchair", "stove", "bathtub", "pill bottle", "escalator", "crosswalk",
                  "traffic light", "shopping cart", "fire extinguisher", "power strip"):
        assert label in VOCABULARY, label
    assert scale_of("pill bottle") == "small" and scale_of("plate") == "small"
    assert scale_of("power strip") == "medium" and scale_of("curb") == "medium", (
        "trip hazards are spoken however low they sit")
    assert scale_of("escalator") == "large" and scale_of("wardrobe") == "large"
    assert {"escalator", "crosswalk"} <= HIGH_PRECISION_CLASSES


def test_a_person_at_a_stove_is_cooking():
    person = seen("person", 0.0, 2.0, box=(0.4, 0.2, 0.6, 0.9))
    stove = seen("stove", 2.0, 2.2, box=(0.35, 0.5, 0.75, 0.95))
    spoken = describe_scan(SceneModel(), [person, stove])
    assert "cooking at a stove" in spoken, spoken


def test_a_person_in_an_armchair_is_sitting():
    person = seen("person", 0.0, 2.0, box=(0.4, 0.3, 0.6, 0.9))
    chair = seen("armchair", 0.0, 2.0, box=(0.35, 0.4, 0.65, 0.95))
    spoken = describe_scan(SceneModel(), [person, chair])
    assert "sitting in an armchair" in spoken, spoken


# --- Every scan ends with the walkway ---------------------------------------


def test_a_scan_ends_with_a_blocked_walkway():
    spoken = describe_scan(SceneModel(), [seen("chair", 5.0, 0.8), seen("door", 2.0, 4.0)])
    assert spoken.endswith("The way ahead is blocked by a chair less than a meter straight ahead."), spoken


def test_a_clear_walkway_says_what_it_leads_to():
    spoken = describe_scan(SceneModel(), [seen("chair", 40.0, 1.0), seen("door", 3.0, 4.0)])
    assert spoken.endswith(
        "The way ahead looks clear, as far as I can tell, and leads to a door about 4 meters ahead."
    ), spoken


def test_a_clear_walkway_with_nothing_ahead_says_so():
    spoken = describe_scan(SceneModel(), [seen("couch", 60.0, 2.0)])
    assert spoken.endswith("but I can't see what it leads to."), spoken


def test_a_hand_held_thing_is_not_what_the_way_leads_to():
    spoken = describe_scan(SceneModel(), [seen("cup", 0.0, 2.0), seen("table", 1.0, 3.5)])
    assert "leads to a table" in spoken and "cup" not in spoken, spoken


def test_stairs_ahead_are_named_even_without_a_distance():
    spoken = describe_scan(SceneModel(), [seen("stairs", 2.0, None)])
    assert "Straight ahead there are stairs; I can't tell how far." in spoken, spoken


def test_the_fallback_scene_description_ends_with_the_walkway_too():
    from backend.scene.inference import describe_scene
    from backend.tests.test_scene import build_scene, make_detection

    scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=1.2)])
    spoken = describe_scene(scene)
    assert spoken.endswith("The way ahead is blocked by a chair about 1 meter straight ahead."), spoken


def test_a_wall_is_what_a_clear_way_leads_to_and_is_never_listed():
    spoken = describe_scan(SceneModel(), [seen("wall", 0.0, None), seen("chair", 50.0, 2.0)])
    assert spoken.endswith("leads to a wall; I can't tell how far."), spoken
    assert spoken.count("wall") == 1, spoken
    with_door = describe_scan(SceneModel(), [seen("wall", 0.0, None), seen("door", 4.0, 3.0)])
    assert "leads to a door about 3 meters ahead" in with_door and "wall" not in with_door, with_door


def test_a_thing_the_camera_moved_off_still_counts_in_both_frames():
    """Hand-held: the second frame is shifted, the boxes no longer overlap,
    but the same chair in the same direction at the same size is the same
    chair."""
    first = [detection("chair", 100, 100, 200, 300, 0.7, azimuth=-5.0)]
    second = [detection("chair", 260, 110, 360, 310, 0.75, azimuth=-1.0)]
    both, once = match_scan_frames([first, second], (640, 480))
    assert [s.label for s in both] == ["chair"] and once == []
    far = [detection("chair", 260, 110, 360, 500, 0.75, azimuth=20.0)]
    both, once = match_scan_frames([first, far], (640, 480))
    assert both == [] and len(once) == 2


def test_people_come_before_things_whatever_the_distance():
    spoken = describe_scan(SceneModel(), [
        seen("table", 0.0, 1.5), seen("chair", 3.0, 1.6),
        seen("person", -20.0, 4.0, box=(0.2, 0.3, 0.3, 0.9)),
    ])
    assert spoken.index("a person") < spoken.index("a table"), spoken
