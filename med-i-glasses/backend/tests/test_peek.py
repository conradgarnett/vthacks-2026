"""Background reading: peeks are cheap, few, short-lived, and make Read instant.

The client sends one detailed frame every few seconds. The server reads it
with a single cheap pass, keeps only the last few results for a few seconds,
and a Read answers from them when they are fresh and solid. Nothing here may
queue up, block the socket, or grow without bound.
"""

from __future__ import annotations

import asyncio
import io
import time

import pytest
from PIL import Image, ImageDraw, ImageFilter

from backend.ai.ocr import TextLine, TextReader
from backend.main import (
    PEEK_FRESH_S,
    PEEK_KEEP_MAX,
    PEEK_KEEP_S,
    PEEK_TAG,
    Session,
    unpack_tagged_frames,
)


def frame(blur: float = 0.0) -> bytes:
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)
    for row in range(6):
        draw.text((60, 50 + row * 100), "EXIT THIS WAY", fill="black")
        draw.rectangle([60, 90 + row * 100, 800, 96 + row * 100], fill="black")
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def line(text: str) -> TextLine:
    return TextLine(text=text, confidence=0.9, top=0.3, left=0.1, height=0.05)


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    def spoken(self) -> list[str]:
        return [p["text"] for p in self.sent if p.get("type") == "speech"]


class FakeReader(TextReader):
    """Its quick pass is deliberately unsure (0.5), so the fast tier never
    settles here and these tests see the peek cache and the burst alone."""

    name = "fake"
    confidence_informative = True

    def __init__(self, lines: list[TextLine]) -> None:
        self.lines = lines
        self.quick_calls = 0
        self.burst_calls = 0

    @property
    def available(self) -> bool:
        return True

    async def read_quick(self, frame_jpeg: bytes) -> list[TextLine]:
        self.quick_calls += 1
        return [TextLine(l.text, 0.5, l.top, l.left, l.height) for l in self.lines]

    async def read_consensus(self, frames: list[bytes]) -> list[TextLine]:
        self.burst_calls += 1
        return list(self.lines)


class FakeScene:
    def all_objects(self):
        return []


class FakePerception:
    scene = FakeScene()


class FakeProvider:
    reads_text = False


def make_session(lines: list[TextLine]) -> tuple[Session, FakeReader, FakeSocket]:
    reader = FakeReader(lines)
    socket = FakeSocket()
    return Session(socket, FakeProvider(), FakePerception(), reader), reader, socket


class TestFraming:
    def test_a_peek_frame_round_trips(self):
        payload = PEEK_TAG + len(b"jpeg").to_bytes(4, "big") + b"jpeg"
        assert unpack_tagged_frames(payload, PEEK_TAG) == [b"jpeg"]

    def test_a_truncated_peek_is_dropped_not_guessed(self):
        payload = PEEK_TAG + (10).to_bytes(4, "big") + b"short"
        assert unpack_tagged_frames(payload, PEEK_TAG) == []


@pytest.mark.asyncio
async def test_a_sharp_peek_is_read_and_kept():
    session, reader, _ = make_session([line("EXIT THIS WAY")])
    await session.handle_peek([frame()])
    assert session.peek_task is not None
    await session.peek_task
    assert reader.quick_calls == 1
    assert len(session.peeks) == 1


@pytest.mark.asyncio
async def test_a_soft_frame_is_not_worth_a_pass():
    session, reader, _ = make_session([line("EXIT THIS WAY")])
    await session.handle_peek([frame(blur=6.0)])
    assert session.peek_task is None
    assert reader.quick_calls == 0


@pytest.mark.asyncio
async def test_no_peek_while_a_read_burst_is_in_flight():
    session, reader, _ = make_session([line("EXIT THIS WAY")])
    session.reading = True
    await session.handle_peek([frame()])
    assert reader.quick_calls == 0


@pytest.mark.asyncio
async def test_peeks_never_queue_up():
    """A second peek while one is running is dropped, not queued: a queued
    peek would describe a label the user has already moved."""
    session, reader, _ = make_session([line("EXIT THIS WAY")])
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_read(frame_jpeg: bytes) -> list[TextLine]:
        started.set()
        await release.wait()
        return [line("EXIT THIS WAY")]

    reader.read_quick = slow_read  # type: ignore[method-assign]
    await session.handle_peek([frame()])
    await started.wait()
    await session.handle_peek([frame()])
    release.set()
    await session.peek_task
    assert len(session.peeks) == 1


def test_stale_peeks_are_forgotten_and_the_cache_is_bounded():
    session, _, _ = make_session([])
    now = time.monotonic()
    session.peeks.append((now - PEEK_KEEP_S - 1, [line("OLD")]))
    for _ in range(PEEK_KEEP_MAX + 3):
        session.peeks.append((now, [line("NEW")]))
    assert len(session.peeks) == PEEK_KEEP_MAX
    session.forget_stale_peeks(now)
    assert all(text == "NEW" for _, lines in session.peeks for text in [lines[0].text])


def test_a_reading_older_than_the_moment_is_not_fresh():
    session, _, _ = make_session([])
    now = time.monotonic()
    session.peeks.append((now - PEEK_FRESH_S - 0.5, [line("EXIT")]))
    assert session.fresh_reading(now) == []


@pytest.mark.asyncio
async def test_read_answers_from_fresh_peeks_without_a_burst():
    session, reader, socket = make_session([line("EXIT THIS WAY")])
    now = time.monotonic()
    session.peeks.append((now - 2.0, [line("EXIT THIS WAY")]))
    session.peeks.append((now - 0.5, [line("EXIT THIS WAY")]))
    await session.handle_read([frame()])
    assert reader.burst_calls == 0
    assert socket.spoken() == ["It reads: EXIT THIS WAY."]


@pytest.mark.asyncio
async def test_read_falls_back_to_the_burst_with_nothing_fresh():
    session, reader, socket = make_session([line("EXIT THIS WAY")])
    await session.handle_read([frame()])
    assert reader.burst_calls == 1
    assert socket.spoken() == ["It reads: EXIT THIS WAY."]


@pytest.mark.asyncio
async def test_a_weak_cached_reading_does_not_replace_the_burst():
    """A scrap from a peek is not an answer; the burst gets its full try."""
    session, reader, _ = make_session([line("EXIT THIS WAY")])
    session.peeks.append((time.monotonic(), [line("il")]))
    await session.handle_read([frame()])
    assert reader.burst_calls == 1
