"""Ask online, everything else on-device, and never silent.

The NVIDIA endpoint is faked with an httpx transport, so these run without a
key or a network and assert on what would have been sent.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from backend.ai.local_provider import LocalSceneProvider
from backend.ai.nvidia_provider import CUT_OFF, MAX_IMAGE_B64, OFFLINE_PREFIX, NvidiaVisionProvider, shrink_jpeg
from backend.ai.vision import build_provider
from backend.config import Settings
from backend.tests.test_scene import build_scene, make_detection


def sse(*texts: str) -> str:
    events = "".join(
        "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n" for text in texts
    )
    return events + "data: [DONE]\n\n"


class FakeNvidia:
    def __init__(self, status: int = 200, texts=("There's a chair ", "about two meters ahead.")):
        self.status = status
        self.texts = texts
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "meta/llama-3.2-11b-vision-instruct"}]})
        if self.status != 200:
            return httpx.Response(self.status, text='{"error": "no"}')
        return httpx.Response(200, text=sse(*self.texts), headers={"content-type": "text/event-stream"})


def provider(fake: FakeNvidia, scene=None) -> NvidiaVisionProvider:
    settings = Settings(vision_provider="nvidia", nvidia_api_key="test-key", _env_file=None)
    scene = scene or build_scene([make_detection(label="chair", distance=2.0, azimuth=0.0)])
    local = LocalSceneProvider(lambda: scene)
    return NvidiaVisionProvider(settings, local, client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))


async def collect(p: NvidiaVisionProvider, prompt: str, intent: str, frame: bytes = b"jpeg", context=None) -> str:
    return "".join([chunk async for chunk in p.describe(frame, prompt, context, intent=intent)])


@pytest.mark.asyncio
async def test_a_question_is_answered_online_with_the_picture_and_the_scene():
    fake = FakeNvidia()
    spoken = await collect(provider(fake), "Is there a chair?", "ask", frame=b"jpegbytes", context="SCENE MODEL: chair")
    assert spoken == "There's a chair about two meters ahead."
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.headers["Authorization"] == "Bearer test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "meta/llama-3.2-11b-vision-instruct" and payload["stream"] is True
    user = payload["messages"][-1]["content"]
    assert isinstance(user, list) and user[0]["type"] == "text"
    assert "Is there a chair?" in user[0]["text"] and "SCENE MODEL: chair" in user[0]["text"]
    assert user[1]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"jpegbytes").decode()
    assert payload["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_the_inline_style_puts_the_picture_in_the_text():
    fake = FakeNvidia()
    p = provider(fake)
    p._settings.nvidia_image_style = "inline"
    await collect(p, "Is there a chair?", "ask", frame=b"jpegbytes")
    user = json.loads(fake.requests[0].content)["messages"][-1]["content"]
    assert isinstance(user, str) and '<img src="data:image/jpeg;base64,' in user


@pytest.mark.asyncio
async def test_a_scan_and_a_read_stay_on_device():
    fake = FakeNvidia()
    p = provider(fake)
    scan = await collect(p, "Describe this room", "scan")
    read = await collect(p, "Read the sign", "read")
    assert "chair" in scan and "can't read" in read.lower()
    assert fake.requests == [], "nothing may go online for a scan or a read"
    assert p.reads_text is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "How many pills do I take?",
        "What is the dose on this bottle?",
        "How much of this medicine should I take",
        "Where are my meds?",
        "How many mg is this",
    ],
)
async def test_a_medicine_question_never_goes_online_and_points_to_read(question):
    fake = FakeNvidia()
    scene = build_scene([make_detection(label="pill bottle", distance=0.6, azimuth=0.0)])
    spoken = await collect(provider(fake, scene), question, "ask")
    assert fake.requests == [], question
    assert spoken.endswith("press Read: I only speak doses that several frames agree on."), spoken


@pytest.mark.asyncio
async def test_the_model_is_told_never_to_state_a_dose():
    fake = FakeNvidia()
    await collect(provider(fake), "What colour is the door?", "ask")
    system = json.loads(fake.requests[0].content)["messages"][0]["content"]
    assert "Never state a medication dose" in system


@pytest.mark.asyncio
async def test_an_online_failure_falls_back_to_the_scene_answer_with_a_warning():
    fake = FakeNvidia(status=500)
    spoken = await collect(provider(fake), "Where is the chair?", "ask")
    assert spoken.startswith(OFFLINE_PREFIX) and "chair" in spoken and "2.0 meters" in spoken


@pytest.mark.asyncio
async def test_a_stream_that_dies_mid_answer_says_so_instead_of_trailing_off():
    class Dying(FakeNvidia):
        def handler(self, request):
            self.requests.append(request)
            return httpx.Response(200, text='data: {"choices": [{"delta": {"content": "The door is"}}]}\n\ndata: {not json\n\n')

    spoken = await collect(provider(Dying()), "Where is the door?", "ask")
    assert spoken == "The door is" + CUT_OFF


@pytest.mark.asyncio
async def test_verify_reports_the_key_and_the_model():
    p = provider(FakeNvidia())
    await p.verify()
    assert "accepted" in p.note and "available" in p.note
    rejected = provider(FakeNvidia(status=401))

    def unauthorized(request):
        return httpx.Response(401, text="unauthorized")

    rejected._client = httpx.AsyncClient(transport=httpx.MockTransport(unauthorized))
    await rejected.verify()
    assert "rejected" in rejected.note


def test_a_big_frame_is_shrunk_under_the_inline_limit():
    import cv2
    import numpy as np

    noise = np.random.default_rng(1).integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
    big = cv2.imencode(".jpg", noise, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()
    assert len(big) * 4 // 3 > MAX_IMAGE_B64
    small = shrink_jpeg(big)
    assert len(base64.b64encode(small)) <= MAX_IMAGE_B64
    assert shrink_jpeg(b"tiny") == b"tiny"


def test_build_provider_falls_back_to_the_scene_model_without_a_key():
    settings = Settings(vision_provider="nvidia", nvidia_api_key=None, _env_file=None)
    scene = build_scene([make_detection(label="chair")])
    assert type(build_provider(settings, lambda: scene)).__name__ == "LocalSceneProvider"
    with_key = Settings(vision_provider="nvidia", nvidia_api_key="k", _env_file=None)
    assert type(build_provider(with_key, lambda: scene)).__name__ == "NvidiaVisionProvider"
