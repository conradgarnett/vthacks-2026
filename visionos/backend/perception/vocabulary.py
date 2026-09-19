"""What VisionOS looks for, and how big those things are.

COCO's 80 classes were chosen for benchmark diversity, not for walking through
a building. They contain "frisbee" and "hair drier" but no door, no stairs, no
handrail -- the three things a blind person most needs found. An
open-vocabulary detector takes free-text classes, so the vocabulary becomes a
design decision rather than a fixed constraint.

Kept deliberately short. Every extra class costs inference time and adds
another chance to hallucinate an obstacle, and a false stair is worse than a
missed lamp.
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


VOCABULARY: dict[str, ClassSpec] = {
    # -- People and animals ------------------------------------------------
    "person": ClassSpec(1.70, True),
    "dog": ClassSpec(0.55, True),
    "cat": ClassSpec(0.30, True),
    # -- Navigation landmarks: the reason for open vocabulary --------------
    "door": ClassSpec(2.00, False, True),
    "doorway": ClassSpec(2.00, False, True),
    "door handle": ClassSpec(0.05, False, True),
    "stairs": ClassSpec(None, True, True),
    "staircase": ClassSpec(None, True, True),
    "handrail": ClassSpec(0.90, False, True),
    "elevator": ClassSpec(2.10, False, True),
    "exit sign": ClassSpec(0.20, False, True),
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
    "laptop": ClassSpec(0.25, False),
    "keyboard": ClassSpec(0.03, False),
    "cell phone": ClassSpec(0.15, False),
    "remote": ClassSpec(0.18, False),
    "book": ClassSpec(0.24, False),
    "tv": ClassSpec(0.60, False),
    "microwave": ClassSpec(0.30, False),
    "refrigerator": ClassSpec(1.70, True),
    "sink": ClassSpec(0.25, False),
    "toilet": ClassSpec(0.75, False),
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

# Open-vocabulary confidence is not comparable to a closed-set detector's, and
# these classes are the ones that cause harm when wrong: a phantom staircase
# will stop someone dead, and a phantom door sends them into a wall.
HIGH_PRECISION_CLASSES = frozenset(
    {"stairs", "staircase", "door", "doorway", "elevator", "low ceiling"}
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
