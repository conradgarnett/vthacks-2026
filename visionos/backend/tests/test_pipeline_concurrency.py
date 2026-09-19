"""Regression: concurrent GPU access killed the backend.

PyTorch's MPS backend is not thread-safe. With the detector and the depth
model on separate thread-pool threads, two threads encoded to one Metal
command buffer and the process died on an assertion -- a hard crash, not an
exception, so it presented to the user as an endless reconnect loop rather
than as an error.

These tests assert the two properties that prevent it: all inference is
serialized on one worker, and frames are dropped rather than queued.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from backend.config import get_settings
from backend.perception.pipeline import PerceptionPipeline
from backend.perception.runtime import inference_pool


def frame_jpeg(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, "JPEG", quality=70)
    return buffer.getvalue()


def test_inference_pool_has_exactly_one_worker():
    """More than one worker reintroduces the Metal crash."""
    assert inference_pool()._max_workers == 1


@pytest.mark.asyncio
async def test_concurrent_frames_do_not_crash_the_process():
    """The shape that used to kill the backend: frames faster than the GPU."""
    pipeline = PerceptionPipeline(get_settings())
    pipeline.warmup()
    if not pipeline.enabled:
        pytest.skip("detector unavailable")

    payload = frame_jpeg()
    await asyncio.gather(*(pipeline.process(payload) for _ in range(12)))

    # Surviving is the assertion; a regression takes the interpreter with it.
    assert pipeline.snapshot()["sense"] == "sight"


@pytest.mark.asyncio
async def test_overlapping_frames_are_dropped_not_queued():
    """A queued backlog means answers describe a room already left behind."""
    pipeline = PerceptionPipeline(get_settings())
    pipeline.warmup()
    if not pipeline.enabled:
        pytest.skip("detector unavailable")

    payload = frame_jpeg()
    await asyncio.gather(*(pipeline.process(payload) for _ in range(8)))

    assert pipeline.dropped_frames > 0, "expected backpressure to drop frames"


@pytest.mark.asyncio
async def test_pipeline_recovers_and_keeps_processing_after_drops():
    pipeline = PerceptionPipeline(get_settings())
    pipeline.warmup()
    if not pipeline.enabled:
        pytest.skip("detector unavailable")

    await asyncio.gather(*(pipeline.process(frame_jpeg()) for _ in range(6)))
    await pipeline.process(frame_jpeg("black"))

    # A dropped frame must not leave the pipeline permanently busy.
    assert pipeline.last_trace.get("stages", {}).get("detect") is not None
