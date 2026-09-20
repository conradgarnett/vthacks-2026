"""A second opinion on a label's text, from the NVIDIA model this machine
already uses for Ask.

The keyword matcher in allergy.py finds the words a label prints for an
allergen. It cannot know that whey and casein are milk, that semolina and
durum are wheat, or that albumin is egg: that is linguistic reasoning, and a
language model is good at it. So the OCR'd lines and the wearer's allergen
list, and nothing else, go to the model, which answers in a fixed shape:

    {"verdict": "confirm" | "deny" | "check", "allergens": [...], "reason": "..."}

Three rules, agreed with the other agent on PR #2 (2026-09-20):

  - Text in, never a class. A food class ("cookie") gives the model nothing
    to reason from and it would guess fluently; the label's own words are
    the only input.
  - "check" is the answer to everything that is not a clean answer: no key,
    no network, a timeout, prose instead of JSON, an allergen outside the
    list. Silence is never an option, so the watch turns "check" into "I
    can't tell what is in this; check the label before eating."
  - The model never earns an email. Its "confirm" is spoken firmly; the
    doctor is emailed only on authoritative evidence (a database hit or a
    clearly read CONTAINS line), which the watch decides without it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable

import httpx

from backend.alerts.profile import normalize_allergen
from backend.config import Settings

log = logging.getLogger(__name__)

VERDICTS = ("confirm", "deny", "check")
TIMEOUT_S = 12.0
MAX_LABEL_CHARS = 2000
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_PROMPT = (
    "You check food labels for a blind person who has allergies. You are given ONLY the text "
    "that a camera read from a package label (it may contain OCR mistakes) and the person's "
    "allergen list. Decide from the text alone.\n"
    "- 'confirm': the text states, or lists an ingredient that is or is derived from, one of "
    "their allergens. Whey, casein, lactose and butter are dairy; semolina, durum, spelt and "
    "barley malt are gluten; albumin is egg; tahini is sesame; groundnut is peanut.\n"
    "- 'deny': the text clearly lists the ingredients and none is, or derives from, their "
    "allergens. A 'free from' or 'no X' statement about an allergen is not a presence.\n"
    "- 'check': the text is too short, unclear, not an ingredient list, or you are not sure.\n"
    "Never guess from a product's name or type alone: a name is not an ingredient list. "
    "'May contain' or 'traces of' an allergen is 'check', not 'confirm'.\n"
    "You may also be shown the picture of the packet. Use it to read the label or to recognize "
    "the exact product, never to guess from the kind of food. Every answer states what it observed "
    "in 'evidence': quote the label text you read, or name the product you recognized. A 'confirm' "
    "without evidence is not allowed; answer 'check' instead.\n"
    "Answer with JSON only, nothing else: "
    '{"verdict": "confirm" | "deny" | "check", "allergens": ["<names from their list that apply>"], '
    '"evidence": "<the label text you read, or the product you recognized>", '
    '"reason": "<one short sentence, no percentages>"}'
)


@dataclass(frozen=True)
class Verdict:
    kind: str  # confirm | deny | check
    allergens: tuple[str, ...]
    reason: str
    source: str  # model | unavailable | unparsable | off
    # What the model says it observed: the label text it read or the
    # product it recognized. A confirm has to have one.
    evidence: str = ""

    @property
    def from_model(self) -> bool:
        return self.source == "model"


UNAVAILABLE = Verdict("check", (), "the online helper did not answer", "unavailable")
OFF = Verdict("check", (), "no online helper is configured", "off")


def label_text(lines: Iterable) -> str:
    text = "\n".join(str(getattr(line, "text", "") or "").strip() for line in lines)
    return "\n".join(part for part in text.split("\n") if part)[:MAX_LABEL_CHARS]


def parse_verdict(content: str, allergens: Iterable[str]) -> Verdict | None:
    """The model's answer as a Verdict, or None when it is not the shape
    asked for. Allergens outside the wearer's list are dropped; a 'confirm'
    left with none becomes 'check'."""
    wanted = {normalize_allergen(str(a)) for a in allergens}
    match = _JSON_OBJECT.search(content or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("verdict") or "").strip().lower()
    if kind not in VERDICTS:
        return None
    raw = data.get("allergens") or []
    if isinstance(raw, str):
        raw = [raw]
    named = tuple(dict.fromkeys(
        normalize_allergen(str(a)) for a in raw if normalize_allergen(str(a)) in wanted
    ))
    reason = " ".join(str(data.get("reason") or "").split())[:200]
    evidence = " ".join(str(data.get("evidence") or "").split())[:300]
    # A confirm has to name an allergen from the list and say what it saw;
    # "cookies usually contain nuts" is neither.
    if kind == "confirm" and (not named or not evidence):
        kind = "check"
    return Verdict(kind, named, reason, "model", evidence)


class LabelReasoner:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self._settings = settings
        self._client = client
        self.timeout_s = timeout_s

    @property
    def available(self) -> bool:
        return self._settings.vision_provider == "nvidia" and bool(self._settings.nvidia_api_key)

    def build_payload(self, text: str, allergens: Iterable[str], frame_jpeg: bytes | None = None) -> dict:
        names = ", ".join(dict.fromkeys(normalize_allergen(str(a)) for a in allergens)) or "none"
        prompt = f"Allergens: {names}\nLabel text as read by the camera:\n{text or '(none read)'}"
        content: str | list = prompt
        if frame_jpeg:
            # The picture too, in the form the Ask path measured as the one
            # the model actually sees (an image_url part, not an inline tag).
            import base64

            from backend.ai.nvidia_provider import shrink_jpeg

            image_b64 = base64.standard_b64encode(shrink_jpeg(frame_jpeg)).decode()
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]
        return {
            "model": self._settings.nvidia_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "max_tokens": 240,
            "temperature": 0.0,
            "stream": False,
        }

    async def judge(self, lines: Iterable, allergens: Iterable[str], frame_jpeg: bytes | None = None) -> Verdict:
        """The model's verdict on the label; 'check' for everything that is not one."""
        if not self.available:
            return OFF
        text = label_text(lines)
        allergens = list(allergens)
        if (not text and not frame_jpeg) or not allergens:
            return Verdict("check", (), "nothing to read", "model")
        client = self._client or httpx.AsyncClient(timeout=self.timeout_s)
        owned = self._client is None
        try:
            response = await client.post(
                f"{self._settings.nvidia_base_url}/chat/completions",
                json=self.build_payload(text, allergens, frame_jpeg),
                headers={"Authorization": f"Bearer {self._settings.nvidia_api_key}", "Accept": "application/json"},
                timeout=self.timeout_s,
            )
            if response.status_code != 200:
                log.warning("label reasoner: NVIDIA answered %s", response.status_code)
                return UNAVAILABLE
            choices = response.json().get("choices") or [{}]
            content = str((choices[0].get("message") or {}).get("content") or "")
        except Exception as exc:  # noqa: BLE001 - every failure is 'check'
            log.warning("label reasoner failed: %s", exc)
            return UNAVAILABLE
        finally:
            if owned:
                await client.aclose()
        verdict = parse_verdict(content, allergens)
        if verdict is None:
            log.warning("label reasoner: answer was not the JSON asked for: %r", content[:120])
            return Verdict("check", (), "the online helper's answer was unclear", "unparsable")
        return verdict

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
