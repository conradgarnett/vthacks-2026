"""A rehearsal label stands in for the OCR on one demo bottle, and nowhere
else: only with a pill bottle in view or the label's own name or drug in
the read, only when DEMO_LABEL is set, and loudly logged."""

from __future__ import annotations

import pytest

from backend.ai.ocr import TextLine
from backend.main import Session, demo_label_words
from backend.scene.model import SceneModel, SceneObject
from backend.tests.test_peek import FakeProvider, FakeReader, FakeSocket, frame, line

LABEL = "Peter Savage. lis-dex-amphetamine 40 mg capsule. Take 1 capsule by mouth every day for 90 days."


class Perception:
    def __init__(self, labels: list[str]) -> None:
        self.scene = SceneModel()
        for index, label in enumerate(labels):
            self.scene.objects[index] = SceneObject(
                object_id=index, label=label, azimuth_deg=0.0, distance_m=0.5, confidence=0.9,
                first_seen=0.0, last_seen=0.0, visible=True, is_obstacle=False,
            )
        self.detector = None


def session(labels: list[str], read: list[TextLine], demo: str = LABEL) -> tuple[Session, FakeSocket, FakeReader]:
    socket = FakeSocket()
    reader = FakeReader(read)
    made = Session(socket, FakeProvider(), Perception(labels), reader)
    made.demo_label = demo
    made.demo_holders = {"pill bottle", "bottle"}
    return made, socket, reader


def test_the_labels_distinctive_words_are_the_name_and_the_drug():
    words = demo_label_words(LABEL)
    assert "savage" in words and "peter" in words
    assert "lisdexamphetamine" in words and "amphetamine" in words
    assert "capsule" not in words and "mouth" not in words and "every" not in words


@pytest.mark.asyncio
async def test_a_pill_bottle_in_view_gets_the_label_without_reading():
    made, socket, reader = session(["pill bottle"], [line("EXIT")])
    await made.handle_read([frame()])
    assert socket.spoken() == [f"On the pill bottle, it reads: {LABEL}"]
    assert reader.quick_calls == 0 and reader.burst_calls == 0
    assert any(p.get("type") == "trace" for p in socket.sent)


@pytest.mark.asyncio
async def test_a_read_that_matches_the_labels_words_gets_the_label():
    made, socket, reader = session(["chair"], [line("SAVAGE PETER"), line("LISDEXAMPHETAMINE 40MG")])
    await made.handle_read([frame()])
    assert socket.spoken() == [f"On the pill bottle, it reads: {LABEL}"]
    assert reader.burst_calls == 1, "the read ran first and matched"


@pytest.mark.asyncio
async def test_another_sign_is_read_as_itself():
    made, socket, _ = session([], [line("EXIT THIS WAY")])
    await made.handle_read([frame()])
    assert socket.spoken() == ["It reads: EXIT THIS WAY."]


@pytest.mark.asyncio
async def test_without_a_demo_label_a_bottle_is_just_read():
    made, socket, _ = session(["pill bottle"], [line("EXIT THIS WAY")], demo="")
    await made.handle_read([frame()])
    assert socket.spoken() == ["On the pill bottle, it reads: EXIT THIS WAY."]
