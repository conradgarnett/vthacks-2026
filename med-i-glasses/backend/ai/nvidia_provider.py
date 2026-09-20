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
import re
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

# A question about medicine never goes to a general model. Doses come from
# Read, where several frames have to agree before a number is spoken; a
# model guessing "take two" from one picture is the failure this whole
# project is built to avoid. Such a question gets the on-device answer
# (which can still say where the pill bottle is) and a pointer to Read.
MEDICAL_QUESTION = re.compile(
    r"\b(dose|dosage|dosing|mg|milligrams?|tablets?|pills?|capsules?|prescription|"
    r"medication|medicine|meds|how (?:many|much) .{0,24}\btake\b)",
    re.IGNORECASE,
)
READ_FOR_LABELS = " For what a medicine label says, press Read: I only speak doses that several frames agree on."
# Told to the model with every question, in case the question is medical in
# a way the pattern misses.
NO_DOSES = (
    "Never state a medication dose, strength, or how many to take, even if "
    "asked; say to press Read instead."
)
# How to weigh the picture against the detector's list, sent with every
# question. The list carries the detector's confidence per object; the
# user's rule is that nothing under 80% is stated as a fact.
GROUNDING = (
    "Answer from the picture first. The list says what the detector tracked "
    "and how sure it is: at least 80% is a fact you can state; below that, say "
    "'may be'. If you can see something clearly in the picture, say so even if "
    "the list lacks it. Only if you cannot see what was asked about, and the "
    "list lacks it, say you can't see it. Never invent an object, and never "
    "mention the detector, the list, the scene model or percentages: the "
    "listener is blind and hears only your words."
)


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
        scene_getter=None,
    ) -> None:
        self._settings = settings
        self._local = local
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        # The tracked scene, for what the detector looked for and did not
        # find: the strongest evidence against inventing it.
        self._scene = scene_getter
        self.note = ""

    def absent_line(self, prompt: str) -> str | None:
        """'The detector knows what a dog looks like and found none in view',
        when the question names something the vocabulary covers and the
        scene model does not hold. A model told this rarely invents one."""
        from backend.ai.local_provider import asked_about
        from backend.scene.queries import find_object
        from backend.speech.phrasing import with_article

        if self._scene is None:
            return None
        label = asked_about(prompt)
        if not label:
            return None
        scene = self._scene()
        if scene is None or find_object(scene, label):
            return None
        # The detector finds about half of what is there, so its silence is
        # evidence, not proof: the picture stays the tiebreaker.
        return (
            f"The detector did not list {with_article(label)}. If the picture clearly "
            f"shows one, say 'I think I see' and answer; if not, say you can't see one."
        )

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
                self.note = "NVIDIA rejected the API key (401). Check NVIDIA_API_KEY in med-i-glasses/.env."
            elif response.status_code != 200:
                self.note = f"NVIDIA's model list returned {response.status_code}; questions may fail."
            else:
                ids = {m.get("id") for m in response.json().get("data", [])}
                if self.model in ids:
                    self.note = f"NVIDIA key accepted; {self.model} is available."
                else:
                    self.note = (
                        f"NVIDIA key accepted, but {self.model} is not in its model list; "
                        "set NVIDIA_MODEL in med-i-glasses/.env to one that is."
                    )
        except Exception as err:  # noqa: BLE001 - startup must not die on the network
            self.note = f"Could not reach NVIDIA: {err}"
        if "accepted;" in self.note:
            log.info(self.note)
        else:
            log.warning(self.note)

    def build_payload(self, frame_jpeg: bytes, prompt: str, scene_context: str | None) -> dict:
        image_b64 = base64.standard_b64encode(shrink_jpeg(frame_jpeg)).decode()
        absent = self.absent_line(prompt)
        text = (
            (scene_context + "\n" if scene_context else "")
            + (absent + "\n" if absent else "")
            + GROUNDING + "\n\nQuestion: " + prompt
        )
        if self._settings.nvidia_image_style == "parts":
            user_content: str | list = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]
        else:
            user_content = f'{text} <img src="data:image/jpeg;base64,{image_b64}" />'
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + "\n10. " + NO_DOSES},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": self._settings.mediglasses_max_tokens,
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
        if intent != "ask" or MEDICAL_QUESTION.search(prompt):
            async for chunk in self._local.describe(frame_jpeg, prompt, scene_context, intent):
                yield chunk
            if intent == "ask":
                yield READ_FOR_LABELS
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
