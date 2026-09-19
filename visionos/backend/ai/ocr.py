"""Text reading.

Local first, on measurement: Apple's Vision framework reads a sign in ~15 ms
against roughly 1-2 s for an API round trip, needs no credentials, and keeps
working when the network dies. Claude remains the escalation path for what OCR
cannot do -- questions *about* text, handwriting, unusual layouts -- and is
the only option off macOS.

Three things make this work on real camera frames rather than clean renders:

  Plausibility filtering. Vision reports ~0.5 confidence for nearly anything,
  garbage included, so its confidence cannot gate output. text_quality.py
  judges the text itself instead.

  Multi-frame consensus. A hand-held phone produces motion blur, glare and
  bad angles, and OCR noise differs from frame to frame while real text does
  not. Agreement across frames is the signal that separates them.

  Conservative thresholds. Hunting for very small text finds "text" in
  carpet, brick and foliage. Speaking gibberish to someone who cannot check
  the sign is worse than admitting we could not read it.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher

from backend.ai.text_quality import assess, clean_for_speech, normalize

log = logging.getLogger(__name__)

NO_TEXT_FOUND = "I don't see any readable text."
OCR_UNAVAILABLE = "I can't read text right now."

# Vision's default is 1/32 (~0.031) and silently skips anything smaller, which
# is most signage seen across a room. 0.02 recovers that without the noise
# floor: below ~0.01 Vision starts finding "text" in textures.
_MIN_TEXT_HEIGHT = 0.02
_MIN_TEXT_HEIGHT_RETRY = 0.012

# Vision's confidence is coarse. This only drops the obviously-bad; real
# filtering is linguistic.
_MIN_CONFIDENCE = 0.3

# Two lines within this fraction of frame height are one row, so a two-column
# sign reads left-to-right instead of zig-zagging down the page.
_SAME_LINE_TOLERANCE = 0.04

# Below this, upscaling gives Vision more pixels per stroke and measurably
# improves recall on distant text.
_UPSCALE_BELOW_PX = 1100
_MAX_UPSCALE = 2.0


@dataclass(slots=True)
class TextLine:
    text: str
    confidence: float
    # Normalized, origin top-left (Vision's own origin is bottom-left).
    top: float
    left: float
    # Frames this line was seen in. 1 unless it came through consensus.
    agreement: int = 1


class AppleVisionOCR:
    """macOS-native text recognition. No download, no network, no key."""

    def __init__(self) -> None:
        self._available = False
        try:
            import Vision  # noqa: F401

            self._available = True
        except ImportError:
            log.info("Apple Vision unavailable (not macOS or pyobjc missing)")

    @property
    def available(self) -> bool:
        return self._available

    def warmup(self) -> None:
        """First call costs ~315 ms of framework load. Never pay it on stage."""
        if not self._available:
            return
        try:
            from PIL import Image

            buffer = io.BytesIO()
            Image.new("RGB", (64, 32), "white").save(buffer, "JPEG")
            self._recognize(buffer.getvalue(), _MIN_TEXT_HEIGHT)
            log.info("OCR warmed up")
        except Exception:
            log.exception("OCR warmup failed")

    # -- single frame ------------------------------------------------------

    def read_sync(self, frame_jpeg: bytes) -> list[TextLine]:
        """Read one frame, retrying once for smaller text."""
        if not self._available:
            return []

        prepared = _prepare(frame_jpeg)
        lines = self._recognize(prepared, _MIN_TEXT_HEIGHT)
        if not lines:
            lines = self._recognize(prepared, _MIN_TEXT_HEIGHT_RETRY)
        return lines

    async def read(self, frame_jpeg: bytes) -> list[TextLine]:
        from backend.perception.runtime import run_inference

        return await run_inference(self.read_sync, frame_jpeg)

    # -- multi frame -------------------------------------------------------

    def read_consensus_sync(self, frames: list[bytes]) -> list[TextLine]:
        """Read several frames and keep only what they agree on.

        OCR noise is inconsistent between frames while real text is stable, so
        agreement is a far better signal than any single frame's confidence.
        """
        usable = [f for f in frames if f]
        if not usable:
            return []
        if len(usable) == 1:
            return [line for line in self.read_sync(usable[0]) if _keep(line)]

        readings = [self.read_sync(frame) for frame in usable]
        return _merge(readings)

    async def read_consensus(self, frames: list[bytes]) -> list[TextLine]:
        from backend.perception.runtime import run_inference

        return await run_inference(self.read_consensus_sync, frames)

    # -- internals ---------------------------------------------------------

    def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
        import Foundation
        import Vision

        data = Foundation.NSData.dataWithBytes_length_(frame_jpeg, len(frame_jpeg))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(1)  # accurate; fast mode misreads signage
        request.setUsesLanguageCorrection_(True)

        # Best-effort: pyobjc exposes whatever the running macOS supports, and
        # an unavailable setter must not fail the read.
        for setter, value in (
            ("setMinimumTextHeight_", minimum_height),
            ("setRecognitionLanguages_", ["en-US"]),
        ):
            try:
                getattr(request, setter)(value)
            except Exception:  # pragma: no cover - depends on OS version
                log.debug("OCR setting %s unavailable", setter)

        success, error = handler.performRequests_error_([request], None)
        if not success:
            log.warning("OCR request failed: %s", error)
            return []

        lines: list[TextLine] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            confidence = float(candidate.confidence())
            if confidence < _MIN_CONFIDENCE:
                continue

            text = str(candidate.string()).strip()
            verdict = assess(text, confidence)
            if not verdict.keep:
                log.debug("OCR rejected %r: %s", text, verdict.reason)
                continue

            box = observation.boundingBox()
            lines.append(
                TextLine(
                    text=text,
                    confidence=confidence,
                    # Vision's origin is bottom-left; flip so larger `top`
                    # means further down the page.
                    top=1.0 - (box.origin.y + box.size.height),
                    left=box.origin.x,
                )
            )

        return lines


def _prepare(frame_jpeg: bytes) -> bytes:
    """Upscale and normalize contrast. Returns the original on any failure."""
    try:
        from PIL import Image, ImageOps

        image = Image.open(io.BytesIO(frame_jpeg))
        image.load()
        if image.mode != "RGB":
            image = image.convert("RGB")

        # More pixels per stroke measurably helps Vision on distant text.
        longest = max(image.size)
        if longest < _UPSCALE_BELOW_PX:
            factor = min(_MAX_UPSCALE, _UPSCALE_BELOW_PX / longest)
            image = image.resize(
                (int(image.width * factor), int(image.height * factor)),
                Image.LANCZOS,
            )

        # Cutoff ignores the extreme tails, so one glare highlight cannot
        # flatten the rest of the frame.
        image = ImageOps.autocontrast(image, cutoff=1)

        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=92)
        return buffer.getvalue()
    except Exception:
        log.debug("OCR preprocessing failed; using original frame")
        return frame_jpeg


def _keep(line: TextLine) -> bool:
    return assess(line.text, line.confidence).keep


# Two readings this similar are the same line read imperfectly twice. Exact
# matching fails here: degraded frames disagree on characters ("204B" vs
# "2048") while clearly referring to the same text.
_SAME_LINE_SIMILARITY = 0.72


def _group_similar(readings: list[list[TextLine]]) -> list[list[TextLine]]:
    """Cluster lines across frames by similarity rather than exact match."""
    groups: list[list[TextLine]] = []
    keys: list[str] = []

    for reading in readings:
        # Within one frame a repeated line is one observation, not two.
        seen: set[str] = set()
        for line in reading:
            key = normalize(line.text)
            if not key or key in seen:
                continue
            seen.add(key)

            for index, existing in enumerate(keys):
                if SequenceMatcher(None, key, existing).ratio() >= _SAME_LINE_SIMILARITY:
                    groups[index].append(line)
                    break
            else:
                groups.append([line])
                keys.append(key)

    return groups


def _merge(readings: list[list[TextLine]]) -> list[TextLine]:
    """Combine several readings of the same scene.

    Agreement across frames is strong evidence, but it is not the only
    evidence: plausibility filtering already rejects texture noise outright
    (measured 4/4 on carpet, brick, foliage and blinds), so a line seen once
    but clearly word-like is kept. Requiring agreement outright cost more real
    text than it saved -- recall fell from 3/4 to 1/4 on degraded captures.
    """
    merged: list[TextLine] = []

    for group in _group_similar(readings):
        agreement = len(group)
        # The most confident variant, tie-broken by plausibility, so "204B"
        # wins over "2048" when both were seen.
        best = max(
            group,
            key=lambda line: (line.confidence, assess(line.text, line.confidence).score),
        )

        if agreement < 2:
            verdict = assess(best.text, best.confidence)
            # A short unconfirmed fragment is what noise looks like; a longer
            # word-like line stands on its own.
            if verdict.score < 0.4 or len(best.text.strip()) < 4:
                log.debug(
                    "OCR dropped %r: seen once, score %.2f", best.text, verdict.score
                )
                continue

        merged.append(
            TextLine(
                text=best.text,
                confidence=best.confidence,
                top=sum(line.top for line in group) / agreement,
                left=sum(line.left for line in group) / agreement,
                agreement=agreement,
            )
        )

    return merged


def sort_reading_order(lines: list[TextLine]) -> list[TextLine]:
    """Top to bottom, then left to right within a row.

    Sorting by `top` alone zig-zags across a multi-column sign, because two
    side-by-side labels almost never share an exact vertical position.
    """
    if not lines:
        return []

    ordered = sorted(lines, key=lambda line: line.top)
    rows: list[list[TextLine]] = [[ordered[0]]]

    for line in ordered[1:]:
        if abs(line.top - rows[-1][0].top) <= _SAME_LINE_TOLERANCE:
            rows[-1].append(line)
        else:
            rows.append([line])

    return [line for row in rows for line in sorted(row, key=lambda l: l.left)]


def format_for_speech(lines: list[TextLine]) -> str:
    """Render recognized text as something worth hearing.

    Lines are joined with periods so the TTS engine pauses between them; a
    sign run together as one breath is hard to follow.
    """
    if not lines:
        return NO_TEXT_FOUND

    # Stray marks are voiced literally by a speech engine -- "EXIT comma
    # comma" -- so they are stripped rather than passed through.
    parts = [clean_for_speech(line.text) for line in sort_reading_order(lines)]
    body = ". ".join(part for part in parts if part)
    if not body:
        return NO_TEXT_FOUND

    return f"It reads: {body}."
