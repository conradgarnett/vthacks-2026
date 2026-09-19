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
from backend.main import app  # noqa: E402

get_settings.cache_clear()

# Smallest valid JPEG; the replay provider never decodes it.
FRAME = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300"
    "0806060706050806070709090806" + "0a" * 50 + "ffd9"
)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_configured_and_active_provider(client):
    """These differ when credentials are missing and the provider fell back,
    so the dashboard must show what is actually serving."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["provider_configured"] == "replay"
    assert body["provider_active"] == "ReplayVisionProvider"


def test_frame_then_scan_returns_spoken_sentences(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "ready"

        ws.send_bytes(FRAME)
        ws.send_json({"type": "scan"})

        speech, trace = [], None
        while trace is None:
            message = ws.receive_json()
            if message["type"] == "speech":
                speech.append(message["text"])
            elif message["type"] == "trace":
                trace = message

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

        speech = []
        while True:
            message = ws.receive_json()
            if message["type"] == "trace":
                break
            speech.append(message["text"])

    spoken = " ".join(speech).lower()
    assert "living room" in spoken, f"expected the room scan, got: {spoken[:80]}"


def test_intent_without_a_frame_says_so_instead_of_hanging(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "scan"})
        assert "camera" in ws.receive_json()["text"].lower()


def test_ask_routes_question_text_to_the_provider(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(FRAME)
        ws.send_json({"type": "ask", "text": "Where is the door?"})

        speech = []
        while True:
            message = ws.receive_json()
            if message["type"] == "trace":
                break
            speech.append(message["text"])

    # Replay keys off the question text, so this proves routing works.
    assert "two o'clock" in " ".join(speech).lower()
