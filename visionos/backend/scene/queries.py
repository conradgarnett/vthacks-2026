"""Queries over the scene model.

These become Claude's tools in Phase 3. Keeping them as plain functions means
the geometry can be tested without an API call, and the same code answers both
the LLM and the deterministic hazard engine.
"""

from __future__ import annotations

from typing import Any

from backend.perception.geometry import in_forward_cone, lateral_offset_m, steps_away
from backend.speech.phrasing import join_spoken, pluralize, with_article
from backend.scene.model import SceneModel, SceneObject

# Some spoken labels don't match COCO's class names.
_ALIASES = {
    "sofa": "couch",
    "settee": "couch",
    "table": "dining table",
    "seat": "chair",
    "television": "tv",
    "screen": "tv",
    "fridge": "refrigerator",
    "someone": "person",
    "somebody": "person",
    "people": "person",
    "man": "person",
    "woman": "person",
}


def _normalize(label: str) -> str:
    cleaned = label.strip().lower()
    return _ALIASES.get(cleaned, cleaned)


def find_object(scene: SceneModel, label: str) -> list[SceneObject]:
    """Substring match so 'chair' finds 'chair' and 'dining chair'."""
    wanted = _normalize(label)
    return [o for o in scene.all_objects() if wanted in o.label.lower()]


def nearest_objects(scene: SceneModel, count: int = 3) -> list[SceneObject]:
    return [o for o in scene.all_objects() if o.distance_m is not None][:count]


def describe_region(scene: SceneModel, region: str) -> list[SceneObject]:
    wanted = region.strip().lower()
    return [o for o in scene.all_objects() if o.region == wanted]


def check_path_clearance(
    scene: SceneModel, width_m: float = 1.0, distance_m: float = 3.0
) -> dict[str, Any]:
    """Is the corridor directly ahead clear?

    Uses real lateral offset rather than a fixed angular cone: a 15-degree cone
    is 0.26 m wide at 1 m but 1.3 m wide at 5 m, so a cone alone would either
    miss near obstacles or cry wolf about distant ones.

    The 1.0 m default is wider than a walking person (~0.5 m) on purpose. The
    two errors are not symmetric: a needless warning is a mild annoyance, a
    missed obstacle is a collision. Tuned after a real photo put a pedestrian
    0.41 m off-centre and an 0.8 m corridor called the path clear.
    """
    half_width = width_m / 2.0
    blockers: list[SceneObject] = []

    for obj in scene.all_objects():
        if obj.distance_m is None or not obj.visible or not obj.is_obstacle:
            continue
        if obj.distance_m > distance_m:
            continue
        if lateral_offset_m(obj.azimuth_deg, obj.distance_m) <= half_width:
            blockers.append(obj)

    blockers.sort(key=lambda o: o.distance_m or 0.0)
    nearest = blockers[0] if blockers else None

    return {
        "clear": not blockers,
        "checked_width_m": width_m,
        "checked_distance_m": distance_m,
        "blockers": [
            {
                "label": o.label,
                "distance_m": o.distance_m,
                "steps": steps_away(o.distance_m or 0.0),
                "clock": o.clock,
            }
            for o in blockers
        ],
        "nearest_blocker": nearest.label if nearest else None,
    }


def objects_in_cone(scene: SceneModel, cone_deg: float, max_distance_m: float) -> list[SceneObject]:
    """Visible obstacles inside the forward cone. Used by the hazard engine."""
    return [
        o
        for o in scene.all_objects()
        if o.visible
        and o.is_obstacle
        and o.distance_m is not None
        and o.distance_m <= max_distance_m
        and in_forward_cone(o.azimuth_deg, cone_deg)
    ]


def what_changed(scene: SceneModel, seconds: float = 10.0) -> list[dict[str, Any]]:
    return [
        {
            "label": o.label,
            "clock": o.clock,
            "distance_m": o.distance_m,
            "seconds_ago": round(max(0.0, o.unseen_s), 1),
        }
        for o in scene.changes_since(seconds)
    ]


def summarize(scene: SceneModel, limit: int = 5) -> str:
    """One spoken sentence covering the nearest objects.

    Duplicates are collapsed: "three people" is easier to act on than three
    separate clauses each naming a person at a slightly different angle.
    """
    objects = [o for o in nearest_objects(scene, limit * 2) if o.distance_m is not None]
    if not objects:
        return "I can't make out anything specific right now."

    grouped: dict[str, list] = {}
    for obj in objects:
        grouped.setdefault(obj.label, []).append(obj)

    parts: list[str] = []
    for label, items in list(grouped.items())[:limit]:
        nearest = min(items, key=lambda o: o.distance_m or 0.0)
        if len(items) > 1:
            parts.append(
                f"{pluralize(len(items), label)}, nearest at your "
                f"{nearest.clock} about {nearest.distance_m:.1f} meters"
            )
        else:
            parts.append(
                f"{with_article(label)} at your {nearest.clock}, "
                f"about {nearest.distance_m:.1f} meters"
            )

    # Verb agrees with the first item: "There's a chair", "There are 5 people".
    leading_count = parts[0].split(" ", 1)[0]
    verb = "There are" if leading_count.isdigit() and leading_count != "1" else "There's"
    return f"{verb} {join_spoken(parts)}."
