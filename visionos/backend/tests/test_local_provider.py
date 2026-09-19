"""The no-credentials path.

The property under test is truthfulness: this provider must describe only what
the detector actually saw, and must decline what it cannot know. Replay's
failure mode -- confidently describing a room the camera isn't pointed at --
is the thing this exists to avoid.
"""

from __future__ import annotations

import pytest

from backend.ai.local_provider import LocalSceneProvider
from backend.tests.test_scene import build_scene, make_detection
from backend.scene.model import SceneModel


async def answer(scene: SceneModel, prompt: str, intent: str | None = None) -> str:
    provider = LocalSceneProvider(lambda: scene)
    chunks = [c async for c in provider.describe(b"", prompt, intent=intent)]
    return "".join(chunks).strip()


@pytest.mark.asyncio
async def test_scan_intent_summarizes_rather_than_answering_about_the_path():
    """Regression: SCAN_PROMPT contains 'walking path', which routed room
    scans to the path answer. Structured intent must win over prompt text."""
    from backend.ai.prompts import SCAN_PROMPT

    scene = build_scene(
        [
            make_detection(label="chair", x=100, distance=1.2, azimuth=0.0),
            make_detection(label="couch", x=400, distance=3.0, azimuth=25.0),
        ]
    )
    spoken = await answer(scene, SCAN_PROMPT, intent="scan")
    assert "chair" in spoken and "couch" in spoken, (
        f"expected a room summary covering both objects, got: {spoken}"
    )


@pytest.mark.asyncio
async def test_read_intent_declines_regardless_of_prompt_wording():
    scene = build_scene([make_detection(label="chair")])
    assert "can't read" in (await answer(scene, "anything", intent="read")).lower()


@pytest.mark.asyncio
async def test_describes_objects_it_actually_sees():
    scene = build_scene([make_detection(label="chair", distance=2.0)])
    spoken = await answer(scene, "Describe this room")
    assert "chair" in spoken and "meters" in spoken


@pytest.mark.asyncio
async def test_declines_to_read_text_rather_than_inventing_it():
    """Reading requires the vision model; guessing here would be fabrication."""
    scene = build_scene([make_detection(label="chair")])
    spoken = await answer(scene, "Read any text visible in this image")
    assert "can't read" in spoken.lower()


@pytest.mark.asyncio
async def test_says_it_cannot_see_an_object_that_is_not_there():
    scene = build_scene([make_detection(label="chair")])
    spoken = await answer(scene, "Where is the person?")
    assert "can't see" in spoken.lower()


@pytest.mark.asyncio
async def test_answers_a_path_question_from_real_geometry():
    scene = build_scene([make_detection(label="chair", azimuth=0.0, distance=1.2)])
    spoken = await answer(scene, "Is the path ahead clear?")
    assert "chair" in spoken and "steps" in spoken


@pytest.mark.asyncio
async def test_path_answer_admits_the_limits_of_its_knowledge():
    """An empty scene means 'nothing recognized', not 'nothing there'."""
    spoken = await answer(SceneModel(), "Is the path ahead clear?")
    assert "recognize" in spoken.lower()


@pytest.mark.asyncio
async def test_locates_a_named_object_with_direction_and_distance():
    scene = build_scene([make_detection(label="chair", azimuth=30.0, distance=2.0)])
    spoken = await answer(scene, "Where is the chair?")
    assert "one o'clock" in spoken and "2.0 meters" in spoken


@pytest.mark.asyncio
async def test_flags_an_object_it_can_no_longer_see():
    """Remembered objects must not be reported as if currently visible."""
    from backend.perception.tracker import Tracker

    tracker, scene = Tracker(), SceneModel()
    tracker.update([make_detection(label="chair")])
    tracker.update([make_detection(label="chair")])
    tracker.update([])  # leaves view
    scene.update(tracker.visible(), tracker.remembered())

    spoken = await answer(scene, "Where is the chair?")
    assert "can't see it now" in spoken.lower()


@pytest.mark.asyncio
async def test_output_is_speakable():
    scene = build_scene([make_detection(label="chair", distance=2.0)])
    spoken = await answer(scene, "Describe this room")
    assert not any(ch in spoken for ch in "*_#[]{}<>")


# --- Hand-held things are answered, not listed --------------------------------


@pytest.mark.asyncio
async def test_a_question_about_cups_is_about_the_cups_not_the_table():
    scene = build_scene(
        [
            make_detection(label="dining table", x=100, distance=1.5, azimuth=0.0),
            make_detection(label="cup", x=300, distance=1.4, azimuth=5.0),
            make_detection(label="cup", x=500, distance=1.6, azimuth=-10.0),
        ]
    )
    spoken = await answer(scene, "Are there any cups on the table?")
    assert "cups" in spoken and "2" in spoken, spoken
    assert not spoken.startswith("The dining table"), spoken


@pytest.mark.asyncio
async def test_phone_is_understood_as_a_cell_phone():
    scene = build_scene([make_detection(label="cell phone", distance=1.0, azimuth=0.0)])
    spoken = await answer(scene, "Is there a phone here?")
    assert "cell phone" in spoken and "1.0 meters" in spoken, spoken


@pytest.mark.asyncio
async def test_a_room_description_leaves_out_hand_held_things():
    scene = build_scene(
        [
            make_detection(label="chair", x=100, distance=2.0),
            make_detection(label="cup", x=300, distance=1.0),
        ]
    )
    spoken = await answer(scene, "Describe this room")
    assert "chair" in spoken and "cup" not in spoken, spoken


@pytest.mark.asyncio
async def test_a_word_inside_another_word_is_not_an_object():
    scene = build_scene([make_detection(label="chair", distance=2.0)])
    spoken = await answer(scene, "Is the seat occupied?")
    assert "chair" in spoken and "cup" not in spoken, spoken


@pytest.mark.asyncio
async def test_a_plain_table_is_found_by_name():
    scene = build_scene([make_detection(label="table", distance=1.5)])
    spoken = await answer(scene, "Where is the table?")
    assert "table" in spoken and "can't see" not in spoken, spoken


@pytest.mark.asyncio
async def test_stairs_are_a_thing_one_can_ask_about():
    scene = build_scene([make_detection(label="stairs", distance=None, azimuth=20.0)])
    spoken = await answer(scene, "Where are the stairs?")
    assert "stairs" in spoken and "can't judge the distance" in spoken, spoken
