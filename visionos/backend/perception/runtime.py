"""A single thread for all GPU inference.

PyTorch's MPS backend is not thread-safe. Running the detector and the depth
model on separate thread-pool threads lets two threads encode to one Metal
command buffer at once, and the process dies on a Metal assertion:

    failed assertion `A command encoder is already encoding to this command
    buffer'

That is a hard crash, not an exception -- no traceback, no recovery, the
server just goes away mid-session. Serializing every model call through one
worker removes the concurrency entirely.

The cost is that a depth pass (117 ms) delays the next detection. That is
acceptable: depth runs on one frame in six, and a slightly late frame is worth
incomparably more than a dead backend.
"""

from __future__ import annotations

import asyncio
import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# One worker, deliberately. Raising this reintroduces the crash.
_INFERENCE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")

atexit.register(lambda: _INFERENCE_POOL.shutdown(wait=False))


async def run_inference(fn: Callable[..., T], *args: Any) -> T:
    """Run a model call on the one inference thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_INFERENCE_POOL, fn, *args)


def inference_pool() -> ThreadPoolExecutor:
    return _INFERENCE_POOL
