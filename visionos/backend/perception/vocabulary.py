"""What VisionOS looks for, and how big those things are.

COCO's 80 classes were chosen for benchmark diversity, not for walking through
a building. They contain "frisbee" and "hair drier" but no door, no stairs, no
handrail -- the three things a blind person most needs found. An
open-vocabulary detector takes free-text classes, so the vocabulary becomes a
design decision rather than a fixed constraint.

Every extra class costs inference time and adds another chance to
hallucinate an obstacle, and a false stair is worse than a missed lamp, so an
addition is measured before it stays: eval/run_detect_eval.py reports detect
time, objects invented on blank textures, and recall and precision on
photographed everyday things (eval/fetch_everyday.py). The list grew from 53
to 118 classes on 2026-09-19 at the user's request; the numbers are in the
log in CLAUDE.md.
"""

from __future__ import annotations

from typing import NamedTuple


class ClassSpec(NamedTuple):
    """One vocabulary entry.

    height_m is the real-world height used for pinhole distance. None means we
    have no reliable prior -- direction is reported, distance is not, because
    a made-up distance is worse than an absent one.
    """

    height_m: float | None
    is_obstacle: bool
    # Navigation targets are things a user asks to be guided to.
    is_landmark: bool = False
    # How big a thing this is, in the sense that decides whether a scan
    # mentions it unasked: "small" is hand-held (a phone, a bottle, a
    # remote), spoken only when the user asks about it and never boxed on
    # the preview; "medium" and "large" are always spoken. Left None, the
    # tier is read from the height prior by scale_of(). Set it where the
    # prior misleads: a closed laptop is as tall as a bottle but is a thing
    # of note, a sink is low but a fixture.
    scale: str | None = None


VOCABULARY: dict[str, ClassSpec] = {
    # -- People and animals ------------------------------------------------
    "person": ClassSpec(1.70, True),
    "dog": ClassSpec(0.55, True),
    "cat": ClassSpec(0.30, True),
    # -- Navigation landmarks: the reason for open vocabulary --------------
    "door": ClassSpec(2.00, False, True),
    "doorway": ClassSpec(2.00, False, True),
    "door handle": ClassSpec(0.05, False, True, scale="small"),
    "stairs": ClassSpec(None, True, True),
    "staircase": ClassSpec(None, True, True),
    "handrail": ClassSpec(0.90, False, True),
    "elevator": ClassSpec(2.10, False, True),
    "exit sign": ClassSpec(0.20, False, True, scale="medium"),
    "sign": ClassSpec(0.30, False, True),
    "window": ClassSpec(1.20, False),
    "hallway": ClassSpec(None, False, True),
    # -- Obstacles you walk into ------------------------------------------
    "chair": ClassSpec(0.90, True),
    "couch": ClassSpec(0.80, True),
    "table": ClassSpec(0.75, True),
    "dining table": ClassSpec(0.75, True),
    "desk": ClassSpec(0.75, True),
    "counter": ClassSpec(0.95, True),
    "bed": ClassSpec(0.60, True),
    "bench": ClassSpec(0.85, True),
    "trash can": ClassSpec(0.70, True),
    # A recycling bin and a small refrigerator are the same tall box to the
    # detector; naming the bin, and the symbol on its front, lets the text
    # prompt separate them. The symbol has no height prior: it is a cue for
    # the object it sits on, never spoken on its own.
    "recycling bin": ClassSpec(1.00, True),
    "recycling symbol": ClassSpec(None, False, scale="small"),
    "dumpster": ClassSpec(1.40, True),
    "box": ClassSpec(0.40, True),
    "backpack": ClassSpec(0.45, True),
    "suitcase": ClassSpec(0.65, True),
    "bicycle": ClassSpec(1.05, True),
    "potted plant": ClassSpec(0.50, True),
    "pole": ClassSpec(2.00, True),
    "pillar": ClassSpec(2.50, True),
    # -- Head-height hazards: dangerous precisely because a cane misses them
    "low ceiling": ClassSpec(None, True),
    "shelf": ClassSpec(0.30, True),
    "cabinet": ClassSpec(0.80, False),
    # -- Vehicles ----------------------------------------------------------
    "car": ClassSpec(1.50, True),
    "bus": ClassSpec(3.00, True),
    "truck": ClassSpec(3.20, True),
    "motorcycle": ClassSpec(1.10, True),
    # -- Tabletop objects: asked about, never walked into ------------------
    "cup": ClassSpec(0.12, False),
    "bottle": ClassSpec(0.25, False),
    "bowl": ClassSpec(0.08, False),
    "laptop": ClassSpec(0.25, False, scale="medium"),
    "keyboard": ClassSpec(0.03, False),
    "cell phone": ClassSpec(0.15, False),
    "remote": ClassSpec(0.18, False),
    "book": ClassSpec(0.24, False),
    "tv": ClassSpec(0.60, False),
    "microwave": ClassSpec(0.30, False),
    "refrigerator": ClassSpec(1.70, True),
    "sink": ClassSpec(0.25, False, scale="medium"),
    "toilet": ClassSpec(0.75, False),
    # -- More of the home: furniture and fixtures you walk into ------------
    "armchair": ClassSpec(0.90, True),
    "stool": ClassSpec(0.70, True),
    "coffee table": ClassSpec(0.45, True),
    "nightstand": ClassSpec(0.60, True),
    "dresser": ClassSpec(1.00, True),
    "wardrobe": ClassSpec(2.00, True),
    "bookshelf": ClassSpec(1.80, True),
    "lamp": ClassSpec(0.55, True),
    "radiator": ClassSpec(0.60, True),
    "fireplace": ClassSpec(1.00, True),
    "ladder": ClassSpec(1.80, True),
    "mirror": ClassSpec(0.80, False),
    # A power strip on the floor is what a cane misses and a foot finds. It
    # is low, so by height it would count as hand-held; it is spoken anyway.
    "power strip": ClassSpec(0.04, True, scale="medium"),
    # -- Kitchen ----------------------------------------------------------
    "oven": ClassSpec(0.90, True),
    "stove": ClassSpec(0.90, True),
    "dishwasher": ClassSpec(0.85, True),
    "washing machine": ClassSpec(0.85, True),
    "coffee maker": ClassSpec(0.35, False),
    "blender": ClassSpec(0.40, False),
    "kettle": ClassSpec(0.25, False),
    "toaster": ClassSpec(0.20, False),
    "pot": ClassSpec(0.15, False),
    "pan": ClassSpec(0.08, False),
    "plate": ClassSpec(0.02, False),
    "drinking glass": ClassSpec(0.12, False),
    "fork": ClassSpec(0.02, False),
    "knife": ClassSpec(0.03, False),
    "spoon": ClassSpec(0.02, False),
    "banana": ClassSpec(0.04, False),
    "apple": ClassSpec(0.08, False),
    # -- Bathroom ---------------------------------------------------------
    "bathtub": ClassSpec(0.55, True),
    "towel": ClassSpec(0.40, False),
    "toilet paper": ClassSpec(0.10, False),
    "toothbrush": ClassSpec(0.02, False),
    "hair dryer": ClassSpec(0.20, False),
    # -- Desk and personal things -----------------------------------------
    "computer mouse": ClassSpec(0.04, False),
    "printer": ClassSpec(0.30, False),
    "scissors": ClassSpec(0.02, False),
    "clock": ClassSpec(0.30, False),
    "vase": ClassSpec(0.25, False),
    "handbag": ClassSpec(0.30, False),
    "umbrella": ClassSpec(0.30, False),
    "shoe": ClassSpec(0.10, False),
    # The thing the user most wants read. Naming it lets a read say "On the
    # pill bottle, it reads ..." and lets a question find it.
    "pill bottle": ClassSpec(0.08, False),
    # -- Out and about ----------------------------------------------------
    "escalator": ClassSpec(None, True, True),
    "ramp": ClassSpec(None, False, True),
    # A curb is a step down at the edge of the road: low, and spoken anyway.
    "curb": ClassSpec(0.15, True, scale="medium"),
    "crosswalk": ClassSpec(None, False, True),
    "traffic light": ClassSpec(1.00, False, True),
    "stop sign": ClassSpec(0.75, False, True),
    "fire hydrant": ClassSpec(0.75, True),
    "parking meter": ClassSpec(1.40, True),
    "bollard": ClassSpec(1.00, True),
    "traffic cone": ClassSpec(0.70, True),
    "mailbox": ClassSpec(1.10, True),
    "fence": ClassSpec(1.20, True),
    "gate": ClassSpec(1.50, False, True),
    "tree": ClassSpec(None, True),
    "shopping cart": ClassSpec(1.00, True),
    "stroller": ClassSpec(1.00, True),
    "wheelchair": ClassSpec(0.95, True),
    "scooter": ClassSpec(1.00, True),
    "vending machine": ClassSpec(1.80, False, True),
    "drinking fountain": ClassSpec(1.00, False, True),
    "fire extinguisher": ClassSpec(0.55, False, True),
    # Deliberately absent: keys, wallet, glasses and similar small personal
    # items. Open-vocabulary detection fires on them constantly -- a street
    # photo produced "glasses, 1.9 meters ahead" at 0.62 confidence -- and
    # they are never navigation-relevant. Per-user enrolled objects are the
    # right home for these, not the always-on vocabulary.
}

CLASS_NAMES: list[str] = list(VOCABULARY)

OBSTACLE_CLASSES = frozenset(k for k, v in VOCABULARY.items() if v.is_obstacle)
LANDMARK_CLASSES = frozenset(k for k, v in VOCABULARY.items() if v.is_landmark)
CLASS_HEIGHTS_M: dict[str, float] = {
    k: v.height_m for k, v in VOCABULARY.items() if v.height_m is not None
}

# Size tiers, read from the height prior unless the entry says otherwise.
# The user's rule: people, chairs and tables are always spoken; laptops and
# things of that size too; phones, bottles, remotes and the rest of what
# fits in a hand only when asked about, and never boxed on the preview.
SCALES = ("small", "medium", "large")
SMALL_BELOW_M = 0.30
LARGE_FROM_M = 0.50


def scale_of(label: str) -> str:
    spec = VOCABULARY.get(label)
    if spec is None:
        # A label the vocabulary does not know (a refined one, say) is
        # spoken: leaving a thing out is the choice that needs a reason.
        return "large"
    if spec.scale is not None:
        return spec.scale
    if spec.height_m is None:
        return "large"
    if spec.height_m < SMALL_BELOW_M:
        return "small"
    if spec.height_m < LARGE_FROM_M:
        return "medium"
    return "large"


def is_small(label: str) -> bool:
    return scale_of(label) == "small"


SMALL_CLASSES = frozenset(k for k in VOCABULARY if is_small(k))

# Open-vocabulary confidence is not comparable to a closed-set detector's, and
# these classes are the ones that cause harm when wrong: a phantom staircase
# will stop someone dead, and a phantom door sends them into a wall.
HIGH_PRECISION_CLASSES = frozenset(
    {"stairs", "staircase", "door", "doorway", "elevator", "low ceiling",
     "escalator", "crosswalk"}
)
HIGH_PRECISION_THRESHOLD = 0.25


def height_for(label: str) -> float | None:
    return CLASS_HEIGHTS_M.get(label)


def is_obstacle(label: str) -> bool:
    return label in OBSTACLE_CLASSES


def confidence_floor(label: str, default: float) -> float:
    """Stricter bar for classes whose false positives are dangerous."""
    if label in HIGH_PRECISION_CLASSES:
        return max(default, HIGH_PRECISION_THRESHOLD)
    return default
