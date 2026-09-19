"""Vision providers.

Two implementations behind one interface:

  ClaudeVisionProvider  live Claude vision, streamed
  ReplayVisionProvider  canned scenes -- no credentials, no network, and
                        byte-identical every run

Replay is not just a test double. It is the demo's insurance policy: if the
venue WiFi dies mid-pitch, `VISION_PROVIDER=replay` keeps the whole pipeline
talking. Both stream token-by-token so downstream TTS chunking behaves
identically either way.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator

from backend.ai.prompts import SYSTEM_PROMPT
from backend.config import Settings

log = logging.getLogger(__name__)

# Spoken when the provider fails. Says what still works -- an assistive tool
# going quiet without explanation is worse than one admitting it is degraded.
DEGRADED_MESSAGE = (
    "I lost my connection, so I can't describe the scene right now. "
    "Obstacle detection is still running."
)


class VisionProvider(ABC):
    """Turns a camera frame plus a question into streamed speech text."""

    # Whether a "read" should escalate here when local OCR finds nothing.
    # The local scene provider cannot read at all, so it stays False there.
    reads_text: bool = False
    # One line for the log and /health about whether the provider is usable.
    note: str = ""

    async def verify(self) -> None:
        """Check credentials and reachability at startup; never raises."""
        return None

    @abstractmethod
    def describe(
        self,
        frame_jpeg: bytes,
        prompt: str,
        scene_context: str | None = None,
        intent: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield answer text incrementally. Must never raise -- degrade instead.

        `intent` is the structured verb ("scan" / "read" / "ask"). Providers
        must branch on it rather than pattern-matching the prompt text: the
        scan prompt contains the words "walking path", which silently routed
        room scans to the path answer in two separate providers.
        """

    async def aclose(self) -> None:
        return None


class ClaudeVisionProvider(VisionProvider):
    reads_text = True

    def __init__(self, settings: Settings) -> None:
        from anthropic import AsyncAnthropic

        # Bare constructor on purpose. It resolves ANTHROPIC_API_KEY, then
        # ANTHROPIC_AUTH_TOKEN, then an `ant auth login` profile on disk.
        # Passing api_key= here would break the keyless profile path.
        self._client = AsyncAnthropic(max_retries=1, timeout=20.0)
        self._settings = settings

    async def describe(
        self,
        frame_jpeg: bytes,
        prompt: str,
        scene_context: str | None = None,
        intent: str | None = None,
    ) -> AsyncIterator[str]:
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(frame_jpeg).decode(),
                },
            }
        ]
        if scene_context:
            content.append({"type": "text", "text": scene_context})
        content.append({"type": "text", "text": prompt})

        try:
            async with self._client.messages.stream(
                model=self._settings.visionos_model,
                max_tokens=self._settings.visionos_max_tokens,
                system=SYSTEM_PROMPT,
                # Effort "low" keeps preamble short and thinking cheap. If we
                # miss the 2.5 s budget, this is the first lever to pull.
                thinking={"type": "adaptive"},
                output_config={"effort": self._settings.visionos_effort},
                messages=[{"role": "user", "content": content}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception:
            # Broad by design: this sits in the user's speech path, and no
            # provider failure is worth a silent assistant.
            log.exception("Claude vision call failed; degrading")
            yield DEGRADED_MESSAGE

    async def aclose(self) -> None:
        await self._client.close()


class ReplayVisionProvider(VisionProvider):
    """Deterministic canned responses, streamed word by word."""

    reads_text = True  # the fixture carries a canned "read" line

    # Chosen to feel like a real stream without inventing latency we don't have.
    WORD_DELAY_S = 0.012

    def __init__(self, settings: Settings) -> None:
        self._scenes = self._load(settings.replay_fixture)

    @staticmethod
    def _load(fixture: str) -> dict[str, str]:
        path = Path(__file__).resolve().parents[2] / fixture
        if path.exists():
            return json.loads(path.read_text())
        log.warning("Replay fixture %s missing; using built-in scene", path)
        return {}

    def _lookup(self, prompt: str, intent: str | None = None) -> str:
        """Structured intent first, then longest matching key.

        Both tie-breakers exist because SCAN_PROMPT contains the phrase
        "walking path": dict order let a short "path" key hijack every room
        scan, which is the demo's headline moment.
        """
        if intent and intent in self._scenes:
            return self._scenes[intent]

        lowered = prompt.lower()
        matches = [
            (len(key), text)
            for key, text in self._scenes.items()
            if key != "default" and key.lower() in lowered
        ]
        if matches:
            return max(matches)[1]
        return self._scenes.get(
            "default",
            "A chair is about two meters ahead at your twelve o'clock, and a "
            "doorway is about four meters away at your two o'clock.",
        )

    async def describe(
        self,
        frame_jpeg: bytes,
        prompt: str,
        scene_context: str | None = None,
        intent: str | None = None,
    ) -> AsyncIterator[str]:
        for word in self._lookup(prompt, intent).split(" "):
            await asyncio.sleep(self.WORD_DELAY_S)
            yield word + " "


def credentials_available() -> bool:
    """Mirror the SDK's own resolution order.

    An unset ANTHROPIC_API_KEY does not mean there are no credentials: the SDK
    falls through to ANTHROPIC_AUTH_TOKEN and then an `ant auth login` profile
    on disk.
    """
    import os
    import shutil
    import subprocess

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if not shutil.which("ant"):
        return False
    try:
        return (
            subprocess.run(
                ["ant", "auth", "status"], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except Exception:
        return False


def build_provider(
    settings: Settings, scene_getter=None, detections_getter=None
) -> VisionProvider:
    if settings.vision_provider == "replay":
        log.warning(
            "Vision provider: REPLAY -- canned text, unrelated to the camera. "
            "Rehearsal only."
        )
        return ReplayVisionProvider(settings)

    if settings.vision_provider == "local":
        from backend.ai.local_provider import LocalSceneProvider

        log.info("Vision provider: local scene model (no credentials required)")
        return LocalSceneProvider(scene_getter, detections_getter)

    if settings.vision_provider == "nvidia":
        from backend.ai.local_provider import LocalSceneProvider

        local = LocalSceneProvider(scene_getter, detections_getter)
        if not settings.nvidia_api_key:
            log.warning(
                "VISION_PROVIDER=nvidia but NVIDIA_API_KEY is not set; on-device "
                "mode. Put the key in visionos/.env."
            )
            return local
        from backend.ai.nvidia_provider import NvidiaVisionProvider

        log.info(
            "Vision provider: NVIDIA %s for questions; scans and reads on-device",
            settings.nvidia_model,
        )
        return NvidiaVisionProvider(settings, local, scene_getter=scene_getter)

    if not credentials_available():
        from backend.ai.local_provider import LocalSceneProvider

        log.warning(
            "No credentials resolved -- falling back to the local scene model. "
            "Run `ant auth login` for full vision. Descriptions will be limited "
            "to recognized objects and cannot include text."
        )
        return LocalSceneProvider(scene_getter, detections_getter)

    log.info("Vision provider: Claude (%s)", settings.visionos_model)
    return ClaudeVisionProvider(settings)
