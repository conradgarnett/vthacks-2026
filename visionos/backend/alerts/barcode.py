"""Barcodes on packaging, and what Open Food Facts says is in the product.

The strongest evidence the allergy scanner has: a database lookup cannot
invent an allergen. OpenCV's own barcode detector reads EAN-13, UPC-A and
EAN-8 from a peek or a read frame (no extra dependency), a checksum throws
out the misreads blur produces, and the code goes to Open Food Facts, which
lists `allergens_tags` and `traces_tags` per product, keyless, for the
price of a descriptive User-Agent.

A code the database does not know, or a network that is down, is a shrug,
not a verdict: the watch falls through to the label's text. Results are
cached per code, so a packet held up for ten seconds costs one request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

import httpx

log = logging.getLogger(__name__)

OFF_BASE_URL = "https://world.openfoodfacts.org/api/v2/product/"
USER_AGENT = "VisionOS/0.1 (assistive glasses; https://github.com/conradgarnett/vthacks-2026)"
LOOKUP_TIMEOUT_S = 5.0
CACHE_S = 3600.0
# Open Food Facts' allergen tags -> the profile's groups. Anything else
# ("en:mustard", "en:celery") keeps its own name after the prefix, so a
# custom allergen in the profile can still match it.
OFF_TAGS: dict[str, str] = {
    "en:peanuts": "peanut",
    "en:nuts": "tree nut",
    "en:milk": "dairy",
    "en:eggs": "egg",
    "en:gluten": "gluten",
    "en:crustaceans": "shellfish",
    "en:molluscs": "shellfish",
    "en:fish": "fish",
    "en:soybeans": "soy",
    "en:sesame-seeds": "sesame",
}


@dataclass(frozen=True)
class ProductFacts:
    code: str
    found: bool
    name: str = ""
    brand: str = ""
    allergens: tuple[str, ...] = ()
    traces: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        parts = [p for p in (self.brand, self.name) if p]
        return " ".join(parts) if parts else f"product {self.code}"


def valid_ean(code: str) -> bool:
    """EAN-8, UPC-A, EAN-13 or GTIN-14 with a correct check digit."""
    if not code or not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(ch) for ch in code]
    body = digits[:-1][::-1]
    total = sum(d * (3 if index % 2 == 0 else 1) for index, d in enumerate(body))
    return (10 - total % 10) % 10 == digits[-1]


def decode_barcodes(frame_jpeg: bytes) -> list[str]:
    """Every valid barcode in the frame, best first; [] when OpenCV has no
    barcode module or nothing decodes."""
    if not frame_jpeg:
        return []
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    barcode = getattr(cv2, "barcode", None)
    if barcode is None or not hasattr(barcode, "BarcodeDetector"):
        return []
    image = cv2.imdecode(np.frombuffer(frame_jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return []
    try:
        result = barcode.BarcodeDetector().detectAndDecode(image)
    except cv2.error:
        return []
    # OpenCV 4.5 to 4.7 return (ok, info, types, points); 4.8 and later
    # (info, types, points).
    info = result[1] if len(result) == 4 else result[0]
    codes: list[str] = []
    for text in info or ():
        text = str(text).strip()
        if valid_ean(text) and text not in codes:
            codes.append(text)
    return codes


def _tag_to_group(tag: str) -> str:
    if tag in OFF_TAGS:
        return OFF_TAGS[tag]
    return tag.split(":", 1)[-1].replace("-", " ").strip().lower()


def parse_product(code: str, payload: dict) -> ProductFacts:
    """Open Food Facts' JSON for one product as facts, or 'not found'."""
    if not isinstance(payload, dict) or payload.get("status") != 1:
        return ProductFacts(code=code, found=False)
    product = payload.get("product") or {}
    name = str(product.get("product_name") or product.get("product_name_en") or "").strip()
    brand = str(product.get("brands") or "").split(",")[0].strip()
    allergens = tuple(dict.fromkeys(_tag_to_group(t) for t in product.get("allergens_tags") or [] if t))
    traces = tuple(dict.fromkeys(_tag_to_group(t) for t in product.get("traces_tags") or [] if t))
    return ProductFacts(code=code, found=True, name=name, brand=brand, allergens=allergens, traces=traces)


class ProductLookup:
    """Open Food Facts, with a cache and a short timeout. `None` from
    `facts()` means the network did not answer; that is different from a
    product the database does not know."""

    def __init__(
        self,
        *,
        base_url: str = OFF_BASE_URL,
        timeout_s: float = LOOKUP_TIMEOUT_S,
        user_agent: str = USER_AGENT,
        client: httpx.AsyncClient | None = None,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_s = timeout_s
        self.user_agent = user_agent
        self.enabled = enabled
        self._client = client
        self._clock = clock
        self._cache: dict[str, tuple[float, ProductFacts | None]] = {}

    async def facts(self, code: str) -> ProductFacts | None:
        if not self.enabled or not valid_ean(code):
            return None
        cached = self._cache.get(code)
        if cached is not None and self._clock() - cached[0] <= CACHE_S and cached[1] is not None:
            return cached[1]
        facts = await self._fetch(code)
        self._cache[code] = (self._clock(), facts)
        return facts

    async def _fetch(self, code: str) -> ProductFacts | None:
        client = self._client or httpx.AsyncClient(timeout=self.timeout_s)
        owned = self._client is None
        try:
            response = await asyncio.wait_for(
                client.get(
                    f"{self.base_url}{code}.json",
                    params={"fields": "product_name,product_name_en,brands,allergens_tags,traces_tags"},
                    headers={"User-Agent": self.user_agent},
                ),
                timeout=self.timeout_s + 1.0,
            )
            if response.status_code == 404:
                return ProductFacts(code=code, found=False)
            if response.status_code != 200:
                log.warning("Open Food Facts answered %s for %s", response.status_code, code)
                return None
            return parse_product(code, response.json())
        except Exception as exc:  # noqa: BLE001 - a lookup that fails is a shrug
            log.warning("Open Food Facts lookup for %s failed: %s", code, exc)
            return None
        finally:
            if owned:
                await client.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
