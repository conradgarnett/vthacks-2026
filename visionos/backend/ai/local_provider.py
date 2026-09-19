"""Truthful descriptions with no network and no credentials.

This is the degraded mode that matters. It speaks only what the local detector
actually sees, so it is never wrong in the way canned text is wrong -- replay
will happily describe a living room while the camera points at a parking lot.

It cannot do open-vocabulary queries or read text; COCO has 80 classes and no
door among them. When asked for something it cannot know, it says so rather
than guessing, which is rule 4 of the system prompt enforced in code.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Callable

from backend.ai.vision import VisionProvider
from backend.perception.detector import CLASS_HEIGHTS_M
from backend.perception.geometry import steps_away
from backend.scene.model import SceneModel
from backend.scene.queries import (
    _ALIASES,
    check_path_clearance,
    find_object,
    summarize,
    what_changed,
)

# Everything we could recognize, not just what is on screen. Asking about a
# known class that is absent must produce "I can't see a person", never a
# summary of something else -- answering a different question than the one
# asked is its own kind of fabrication.
_KNOWN_LABELS: tuple[str, ...] = tuple(
    sorted(
        set(CLASS_HEIGHTS_M) | set(_ALIASES),
        key=len,
        reverse=True,  # longest first: "dining table" before "table"
    )
)

log = logging.getLogger(__name__)

_READ_UNAVAILABLE = (
    "I can't read text without a connection. I can still tell you about "
    "objects I recognize."
)
_PATH_WORDS = ("path", "clear", "ahead", "walk", "go", "safe", "obstacle")
_CHANGE_WORDS = ("change", "changed", "new", "happened")


class LocalSceneProvider(VisionProvider):
    """Answers from the tracked scene model alone."""

    def __init__(self, scene_getter: Callable[[], SceneModel]) -> None:
        self._scene = scene_getter

    async def describe(
        self,
        frame_jpeg: bytes,
        prompt: str,
        scene_context: str | None = None,
        intent: str | None = None,
    ) -> AsyncIterator[str]:
        for chunk in self._answer(prompt, intent).split(" "):
            yield chunk + " "

    def _answer(self, prompt: str, intent: str | None = None) -> str:
        scene = self._scene()
        lowered = prompt.lower()

        # Structured intent wins over prompt text. SCAN_PROMPT contains the
        # words "walking path", which routed room scans to the path answer.
        if intent == "read":
            return _READ_UNAVAILABLE
        if intent == "scan":
            return summarize(scene)

        if "read" in lowered and "text" in lowered:
            return _READ_UNAVAILABLE

        if any(word in lowered for word in _CHANGE_WORDS):
            return self._describe_changes(scene)

        if any(word in lowered for word in _PATH_WORDS):
            return self._describe_path(scene)

        # Match against everything we could recognize, not only what is
        # currently visible, so an absent object gets "I can't see a person"
        # rather than a summary of whatever else happens to be in frame.
        for label in _KNOWN_LABELS:
            if label in lowered:
                return self._describe_object(scene, label)

        return summarize(scene)

    @staticmethod
    def _describe_path(scene: SceneModel) -> str:
        result = check_path_clearance(scene)
        if result["clear"]:
            return "The path ahead looks clear, but I can only see obstacles I recognize."

        nearest = result["blockers"][0]
        return (
            f"There's a {nearest['label']} about {nearest['distance_m']:.1f} meters "
            f"ahead, roughly {nearest['steps']} steps, at your {nearest['clock']}."
        )

    @staticmethod
    def _describe_object(scene: SceneModel, label: str) -> str:
        matches = find_object(scene, label)
        if not matches:
            return f"I can't see a {label} right now."

        obj = matches[0]
        if obj.distance_m is None:
            return f"I can see a {obj.label} at your {obj.clock}, but I can't judge the distance."

        seen = "" if obj.visible else ", though I can't see it now"
        return (
            f"The {obj.label} is at your {obj.clock}, about {obj.distance_m:.1f} "
            f"meters, roughly {steps_away(obj.distance_m)} steps{seen}."
        )

    @staticmethod
    def _describe_changes(scene: SceneModel) -> str:
        changes = what_changed(scene, seconds=10.0)
        if not changes:
            return "Nothing has changed that I noticed."
        first = changes[0]
        return f"A {first['label']} appeared at your {first['clock']}."
