"""End-to-end walking skeleton: frame in -> speech out, over the real socket.

Runs against the replay provider so it needs no credentials and no network --
the same property that makes replay the demo's fallback.
"""

from __future__ import annotations

import os

import pytest

os.environ["VISION_PROVIDER"] = "replay"

from fastapi.testclient import TestClient  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.main import (  # noqa: E402
    OCR_CONSENSUS_FRAMES,
    READ_TAG,
    app,
    pack_read_frames,
    unpack_read_frames,
)

get_settings.cache_clear()

# Smallest valid JPEG; the replay provider never decodes it.
FRAME = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300"
    "0806060706050806070709090806" + "0a" * 50 + "ffd9"
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def collect(ws) -> tuple[list[str], dict]:
    """Speech messages up to and including the trace that ends a request."""
    speech: list[str] = []
    while True:
        message = ws.receive_json()
        if message["type"] == "trace":
            return speech, message
        if message["type"] == "speech":
            speech.append(message["text"])


class TestReadFraming:
    """The burst format is mirrored in ws.ts; a mismatch would read nothing."""

    def test_round_trips_a_burst(self):
        frames = [b"one", b"two two", b"three"]
        assert unpack_read_frames(pack_read_frames(frames)) == frames

    def test_message_is_tagged_and_never_looks_like_a_jpeg(self):
        packed = pack_read_frames([FRAME])
        assert packed.startswith(READ_TAG)
        assert not packed.startswith(b"\xff\xd8")

    def test_truncated_tail_is_dropped_not_guessed(self):
        packed = pack_read_frames([b"whole", b"cut off"])
        assert unpack_read_frames(packed[:-3]) == [b"whole"]

    def test_only_the_most_recent_frames_are_kept(self):
        frames = [bytes([i]) * 4 for i in range(OCR_CONSENSUS_FRAMES + 2)]
        assert unpack_read_frames(pack_read_frames(frames)) == frames[-OCR_CONSENSUS_FRAMES:]

    def test_empty_burst_unpacks_to_nothing(self):
        assert unpack_read_frames(READ_TAG) == []


def test_health_reports_configured_and_active_provider(client):
    """These differ when credentials are missing and the provider fell back,
    so the dashboard must show what is actually serving."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["provider_configured"] == "replay"
    assert body["provider_active"] == "ReplayVisionProvider"
    assert "ocr" in body


def test_frame_then_scan_returns_spoken_sentences(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "ready"

        ws.send_bytes(FRAME)
        ws.send_json({"type": "scan"})
        speech, trace = collect(ws)

    assert speech, "expected spoken output"
    assert "meters" in " ".join(speech)
    # The number the demo is judged on.
    assert "first_sentence" in trace["stages"]


def test_scan_returns_the_room_description_not_the_path_answer(client):
    """Regression: SCAN_PROMPT contains 'walking path', so a short 'path'
    fixture key used to hijack the room scan -- the demo's headline moment."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(FRAME)
        ws.send_json({"type": "scan"})
        speech, _ = collect(ws)

    spoken = " ".join(speech).lower()
    assert "living room" in spoken, f"expected the room scan, got: {spoken[:80]}"


def test_intent_without_a_frame_says_so_instead_of_hanging(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "scan"})
        assert "camera" in ws.receive_json()["text"].lower()


def test_read_without_a_frame_says_so_instead_of_hanging(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "read"})
        assert "camera" in ws.receive_json()["text"].lower()


def test_empty_read_burst_says_so_instead_of_hanging(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(pack_read_frames([]))
        assert "camera" in ws.receive_json()["text"].lower()


def test_ask_routes_question_text_to_the_provider(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(FRAME)
        ws.send_json({"type": "ask", "text": "Where is the door?"})
        speech, _ = collect(ws)

    # Replay keys off the question text, so this proves routing works.
    assert "two o'clock" in " ".join(speech).lower()


def test_read_burst_is_read_and_escalates_when_ocr_finds_nothing(client):
    """A read burst arrives as one tagged message, so it is read directly and
    never becomes a live frame. The fixture JPEG holds no text, so local OCR
    finds nothing and the replay provider's canned line is spoken instead."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(pack_read_frames([FRAME] * OCR_CONSENSUS_FRAMES))
        speech, trace = collect(ws)

    assert "exit" in " ".join(speech).lower()
    assert trace["label"] == "read"


def test_read_intent_reads_the_latest_live_frame(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(FRAME)
        ws.send_json({"type": "read"})
        speech, _ = collect(ws)

    assert "exit" in " ".join(speech).lower()
