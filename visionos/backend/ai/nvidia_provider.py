"""Ask answered by an NVIDIA-hosted vision model; everything else on-device.

The user has an NVIDIA API key and wants it used for questions. NVIDIA's
hosted models (build.nvidia.com) speak the OpenAI chat format at
https://integrate.api.nvidia.com/v1/chat/completions; their vision models
take the picture inline in the message text as an <img src="data:..."> tag
and refuse pictures over roughly 180 KB of base64, so a frame is shrunk to
fit before it goes.

Only "ask" goes online. A scan keeps the on-device picture (the walkway,
the size tiers, the two-frame agreement) and a read keeps the OCR path
with the medication guard: a general model reading a dose is the one
failure worse than silence. If the model does not answer, the on-device
answer is spoken with a warning in front of it, so the user is never left
in silence.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import AsyncIterator

import httpx

from backend.ai.prompts import SYSTEM_PROMPT
from backend.ai.vision import VisionProvider
from backend.config import Settings

log = logging.getLogger(__name__)

# NVIDIA rejects an inline picture above about 180 KB of base64. Stay under.
MAX_IMAGE_B64 = 170_000
OFFLINE_PREFIX = "The online helper didn't answer, so from what I can see: "
CUT_OFF = " I lost the online helper mid-answer."
TIMEOUT_S = 30.0


def shrink_jpeg(frame_jpeg: bytes, limit_b64: int = MAX_IMAGE_B64) -> bytes:
    """Re-encode a frame smaller until its base64 fits the limit."""
    if len(frame_jpeg) * 4 // 3 <= limit_b64:
        return frame_jpeg
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(frame_jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return frame_jpeg
    height, width = image.shape[:2]
    encoded = frame_jpeg
    for target, quality in ((640, 70), (512, 65), (384, 60), (320, 50)):
        scale = min(1.0, target / max(width, 1))
        small = cv2.resize(image, (int(width * scale), int(height * scale))) if scale < 1.0 else image
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            continue
        encoded = buf.tobytes()
        if len(encoded) * 4 // 3 <= limit_b64:
            break
    return encoded


class NvidiaVisionProvider(VisionProvider):
    """Questions online, the rest on-device."""

    reads_text = False  # reads stay with OCR and the medication guard

    def __init__(
        self,
        settings: Settings,
        local: VisionProvider,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._local = local
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self.note = ""

    @property
    def model(self) -> str:
        return self._settings.nvidia_model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.nvidia_api_key}",
            "Accept": "text/event-stream",
        }

    async def verify(self) -> None:
        """Say in the log and in /health whether the key and the model are
        usable. Never raises: a bad key must degrade, not crash."""
        try:
            response = await self._client.get(
                f"{self._settings.nvidia_base_url}/models", headers=self._headers()
            )
            if response.status_code == 401:
                self.note = "NVIDIA rejected the API key (401). Check NVIDIA_API_KEY in visionos/.env."
            elif response.status_code != 200:
                self.note = f"NVIDIA's model list returned {response.status_code}; questions may fail."
            else:
                ids = {m.get("id") for m in response.json().get("data", [])}
                if self.model in ids:
                    self.note = f"NVIDIA key accepted; {self.model} is available."
                else:
                    self.note = (
                        f"NVIDIA key accepted, but {self.model} is not in its model list; "
                        "set NVIDIA_MODEL in visionos/.env to one that is."
                    )
        except Exception as err:  # noqa: BLE001 - startup must not die on the network
            self.note = f"Could not reach NVIDIA: {err}"
        if "accepted;" in self.note:
            log.info(self.note)
        else:
            log.warning(self.note)

    def build_payload(self, frame_jpeg: bytes, prompt: str, scene_context: str | None) -> dict:
        image_b64 = base64.standard_b64encode(shrink_jpeg(frame_jpeg)).decode()
        text = (scene_context + "\n\n" if scene_context else "") + prompt
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f'{text} <img src="data:image/jpeg;base64,{image_b64}" />',
                },
            ],
            "max_tokens": self._settings.visionos_max_tokens,
            "temperature": 0.2,
            "stream": True,
        }

    async def describe(
        self,
        frame_jpeg: bytes,
        prompt: str,
        scene_context: str | None = None,
        intent: str | None = None,
    ) -> AsyncIterator[str]:
        if intent != "ask":
            async for chunk in self._local.describe(frame_jpeg, prompt, scene_context, intent):
                yield chunk
            return

        spoke = False
        try:
            async with self._client.stream(
                "POST",
                f"{self._settings.nvidia_base_url}/chat/completions",
                json=self.build_payload(frame_jpeg, prompt, scene_context),
                headers=self._headers(),
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")[:300]
                    raise RuntimeError(f"NVIDIA answered {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    choices = json.loads(data).get("choices") or [{}]
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        spoke = True
                        yield delta
        except Exception:  # noqa: BLE001 - the speech path never goes silent
            log.exception("NVIDIA answer failed; answering from the scene model")
            if spoke:
                yield CUT_OFF
                return
            yield OFFLINE_PREFIX
            async for chunk in self._local.describe(frame_jpeg, prompt, scene_context, intent):
                yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._local.aclose()
