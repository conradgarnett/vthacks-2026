"""Text reading.

Deviation from the spec, with measurements behind it. The plan was Claude
vision first with a local fallback; it is inverted here because Apple's Vision
framework reads a sign in 13 ms steady state (315 ms first call, warmed at
startup) against roughly 1-2 s for an API round trip, needs no credentials,
and keeps working when the network dies. For a latency-critical assistive tool
that is not a close call.

Claude remains the escalation path: it handles what OCR cannot -- answering
questions *about* text, unusual layouts, handwriting -- and is the only option
off macOS.

Reading order matters. Vision returns observations in no guaranteed order, and
a sign read bottom-up is worse than not read at all, so results are sorted
into human reading order before they are spoken.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import partial

log = logging.getLogger(__name__)

NO_TEXT_FOUND = "I don't see any readable text."
OCR_UNAVAILABLE = "I can't read text right now."

# Vision's confidence is coarse; this only filters obvious noise.
_MIN_CONFIDENCE = 0.3
# Fraction of frame height. Vision defaults to 1/32 (~0.031) and silently
# skips anything smaller, which is most signage seen from across a room.
_MIN_TEXT_HEIGHT = 0.012
# Second pass, only when the first finds nothing: catches small print at the
# cost of more time and more noise.
_MIN_TEXT_HEIGHT_RETRY = 0.005
# Two lines within this fraction of frame height count as the same row, so a
# two-column sign reads left-to-right rather than zig-zagging down the page.
_SAME_LINE_TOLERANCE = 0.04


@dataclass(slots=True)
class TextLine:
    text: str
    confidence: float
    # Normalized, origin top-left (Vision's own origin is bottom-left).
    top: float
    left: float


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
            import io

            from PIL import Image

            buffer = io.BytesIO()
            Image.new("RGB", (64, 32), "white").save(buffer, "JPEG")
            self.read_sync(buffer.getvalue())
            log.info("OCR warmed up")
        except Exception:
            log.exception("OCR warmup failed")

    def read_sync(self, frame_jpeg: bytes) -> list[TextLine]:
        """Read text, retrying once for small or distant text.

        Vision defaults to a minimum text height of 1/32 of the frame and
        silently ignores anything smaller -- which is most real signage, since
        a sign photographed from across a room occupies very little of it.
        The first pass is tuned for that; the retry goes smaller still, and
        only runs when the first pass found nothing, so the common case keeps
        its fast path.
        """
        if not self._available:
            return []

        lines = self._recognize(frame_jpeg, minimum_height=_MIN_TEXT_HEIGHT)
        if not lines:
            lines = self._recognize(frame_jpeg, minimum_height=_MIN_TEXT_HEIGHT_RETRY)
        return lines

    def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
        import Foundation
        import Vision

        data = Foundation.NSData.dataWithBytes_length_(frame_jpeg, len(frame_jpeg))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(1)  # accurate; fast mode misreads signage
        request.setUsesLanguageCorrection_(True)

        # Each of these is best-effort: pyobjc exposes whatever the running
        # macOS supports, and an unavailable setter must not fail the read.
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

            box = observation.boundingBox()
            lines.append(
                TextLine(
                    text=str(candidate.string()).strip(),
                    confidence=confidence,
                    # Vision's origin is bottom-left; flip to top-left so a
                    # larger `top` means further down the page.
                    top=1.0 - (box.origin.y + box.size.height),
                    left=box.origin.x,
                )
            )

        return [line for line in lines if line.text]

    async def read(self, frame_jpeg: bytes) -> list[TextLine]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self.read_sync, frame_jpeg))


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

    parts = [line.text.rstrip(".,;: ") for line in sort_reading_order(lines)]
    body = ". ".join(part for part in parts if part)
    if not body:
        return NO_TEXT_FOUND

    return f"It reads: {body}."
