"""A detector label seen as another, from SEE_AS, before anything else sees it."""

from __future__ import annotations

from backend.config import Settings
from backend.perception.detector import Detector


class FakeBox:
    def __init__(self, cls: int, conf: float, xyxy):
        self.cls = [cls]
        self.conf = [conf]
        self._xyxy = xyxy

    @property
    def xyxy(self):
        class Row:
            def __init__(self, values):
                self._values = values

            def tolist(self):
                return self._values

        return [Row(self._xyxy)]


class FakeResult:
    def __init__(self, names, boxes):
        self.names = names
        self.boxes = boxes


class FakeModel:
    def __init__(self, result):
        self._result = result

    def predict(self, frame, **kwargs):
        return [self._result]


def detect_with(see_as: str):
    import numpy as np

    settings = Settings(see_as=see_as, _env_file=None)
    detector = Detector(settings)
    detector._model = FakeModel(FakeResult({0: "refrigerator", 1: "chair"}, [
        FakeBox(0, 0.9, [100.0, 100.0, 200.0, 440.0]),
        FakeBox(1, 0.9, [400.0, 200.0, 480.0, 380.0]),
    ]))
    return detector.detect_sync(np.zeros((480, 640, 3), dtype=np.uint8))


def test_the_pairs_are_parsed_and_normalized():
    remap = Settings(see_as=" Refrigerator : Trash Can , door:doorway,,bad", _env_file=None).label_remap
    assert remap == {"refrigerator": "trash can", "door": "doorway"}
    assert Settings(see_as="", _env_file=None).label_remap == {}


def test_a_refrigerator_is_seen_as_a_trash_can_with_the_trash_can_height():
    plain = {d.label: d for d in detect_with("")}
    swapped = {d.label: d for d in detect_with("refrigerator:trash can")}
    assert "refrigerator" in plain and "trash can" in swapped and "refrigerator" not in swapped
    # Same box, a shorter thing: the distance comes from the trash can's height prior.
    assert swapped["trash can"].distance_m < plain["refrigerator"].distance_m
    assert swapped["trash can"].is_obstacle
    assert swapped["chair"].label == "chair"
