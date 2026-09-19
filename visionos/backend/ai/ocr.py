"""Text reading with on-device OCR.

Local OCR reads first: it answers in well under a second, needs no
credentials, and keeps working when the network dies. Claude is the
escalation path for what OCR cannot do -- handwriting, unusual layouts, and
questions *about* the text.

Two engines sit behind one interface:

  AppleVisionOCR  macOS only, via pyobjc. About 15 ms per frame once warm.
  RapidOCR        every platform. PaddleOCR models on ONNX Runtime, CPU only,
                  a few hundred milliseconds per frame.

The reader takes the first engine that loads. With none, it says it cannot
read rather than pretending to.

Three things make this work on real camera frames rather than clean renders:

  Plausibility filtering. Engine confidence cannot gate output -- Vision
  reports ~0.5 for nearly anything, garbage included -- so text_quality.py
  judges the text itself.

  Multi-frame consensus. A hand-held phone produces motion blur, glare and
  bad angles, and OCR noise differs from frame to frame while real text does
  not. Agreement across frames is the signal that separates them.

  Reading order. Engines return lines in no guaranteed order, and a sign
  spoken bottom-up is worse than not read at all, so lines are grouped into
  rows and sorted before they are spoken.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Iterable, Protocol

from backend.ai.text_quality import assess, clean_for_speech, normalize
from backend.perception.runtime import run_inference

log = logging.getLogger(__name__)

NO_TEXT_FOUND = "I don't see any readable text."

# Engine confidence is coarse. This only drops the obviously bad; the real
# filtering is linguistic, in text_quality.py.
_MIN_CONFIDENCE = 0.3
# Rows are grouped by each line's own height. Lines with no height fall back
# to this fixed fraction of frame height.
_FALLBACK_ROW_TOLERANCE = 0.04

# Apple Vision skips text shorter than 1/32 (~0.031) of the frame by default,
# which is most signage seen across a room. 0.02 recovers that without the
# noise floor: below ~0.01 Vision starts finding "text" in textures.
_APPLE_MIN_TEXT_HEIGHT = 0.02
_APPLE_MIN_TEXT_HEIGHT_RETRY = 0.012

# Below this, upscaling gives an engine more pixels per stroke and measurably
# improves recall on distant text.
_UPSCALE_BELOW_PX = 1100
_MAX_UPSCALE = 2.0

# Two readings this similar are the same line read imperfectly twice. Exact
# matching fails here: degraded frames disagree on characters ("204B" vs
# "2048") while clearly referring to the same text.
_SAME_LINE_SIMILARITY = 0.72


@dataclass(slots=True)
class TextLine:
    text: str
    confidence: float
    # Normalized to the frame, origin top-left.
    top: float
    left: float
    height: float = 0.0
    # Frames this line was seen in. 1 unless it came through consensus.
    agreement: int = 1

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2.0


class OcrEngine(Protocol):
    name: str

    def read(self, frame_jpeg: bytes) -> list[TextLine]: ...


class AppleVisionOCR:
    """macOS-native text recognition. No download, no network, no key."""

    name = "apple-vision"

    def __init__(self) -> None:
        import Vision  # noqa: F401  (ImportError off macOS or without pyobjc)

    def read(self, frame_jpeg: bytes) -> list[TextLine]:
        """Read one frame, retrying once for smaller text."""
        lines = self.recognize(frame_jpeg, minimum_height=_APPLE_MIN_TEXT_HEIGHT)
        if not lines:
            lines = self.recognize(frame_jpeg, minimum_height=_APPLE_MIN_TEXT_HEIGHT_RETRY)
        return lines

    def recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
        import Foundation
        import Vision

        data = Foundation.NSData.dataWithBytes_length_(frame_jpeg, len(frame_jpeg))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(1)  # accurate; fast mode misreads signage
        request.setUsesLanguageCorrection_(True)
        # Best-effort: pyobjc exposes whatever the running macOS supports.
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
            log.warning("Apple Vision request failed: %s", error)
            return []

        lines: list[TextLine] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            box = observation.boundingBox()
            lines.append(
                TextLine(
                    text=str(candidate.string()),
                    confidence=float(candidate.confidence()),
                    # Vision's origin is bottom-left; flip so a larger `top`
                    # is further down the page.
                    top=1.0 - (box.origin.y + box.size.height),
                    left=box.origin.x,
                    height=box.size.height,
                )
            )
        return lines


class RapidOCR:
    """Cross-platform OCR: PaddleOCR detection and recognition on ONNX Runtime."""

    name = "rapidocr"

    def __init__(self) -> None:
        try:
            from rapidocr import RapidOCR as Engine  # 2.x
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR as Engine  # 1.x
        self._engine = Engine()

    def read(self, frame_jpeg: bytes) -> list[TextLine]:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return []
        height, width = image.shape[:2]

        lines: list[TextLine] = []
        for box, text, score in _rapid_results(self._engine(image)):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            lines.append(
                TextLine(
                    text=str(text),
                    confidence=score,
                    top=min(ys) / height,
                    left=min(xs) / width,
                    height=(max(ys) - min(ys)) / height,
                )
            )
        return lines


def _rapid_results(output) -> list[tuple]:
    """Normalize both RapidOCR return shapes to (box, text, score)."""
    if output is None:
        return []
    if isinstance(output, tuple):  # 1.x: (results, elapsed); results may be None
        return [(r[0], r[1], float(r[2])) for r in output[0] or []]
    boxes = getattr(output, "boxes", None)  # 2.x: RapidOCROutput
    if boxes is None:
        return []
    return [
        (box, text, float(score))
        for box, text, score in zip(boxes, output.txts, output.scores)
    ]


_ENGINES: dict[str, type] = {
    AppleVisionOCR.name: AppleVisionOCR,
    RapidOCR.name: RapidOCR,
}


def prepare_frame(frame_jpeg: bytes) -> bytes:
    """Upscale small frames and normalize contrast. Returns the original on any failure."""
    try:
        from PIL import Image, ImageOps

        image = Image.open(io.BytesIO(frame_jpeg))
        image.load()
        if image.mode != "RGB":
            image = image.convert("RGB")

        # More pixels per stroke measurably helps on distant text.
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
        log.debug("OCR preprocessing failed; using the original frame")
        return frame_jpeg


class TextReader:
    """Async front for whichever engine loaded. Never raises into the speech path.

    Engine calls run on the single inference thread so OCR can never contend
    with the detector for the GPU; see perception/runtime.py.
    """

    def __init__(self, engine: OcrEngine | None) -> None:
        self._engine = engine

    @property
    def available(self) -> bool:
        return self._engine is not None

    @property
    def name(self) -> str:
        return self._engine.name if self._engine else "none"

    # -- single frame ------------------------------------------------------

    def read_sync(self, frame_jpeg: bytes) -> list[TextLine]:
        if self._engine is None or not frame_jpeg:
            return []
        try:
            return clean_lines(self._engine.read(prepare_frame(frame_jpeg)))
        except Exception:
            log.exception("OCR failed; treating the frame as having no text")
            return []

    async def read(self, frame_jpeg: bytes) -> list[TextLine]:
        return await run_inference(self.read_sync, frame_jpeg)

    # -- multi frame -------------------------------------------------------

    def read_consensus_sync(self, frames: list[bytes]) -> list[TextLine]:
        """Read several frames and keep what they agree on.

        OCR noise is inconsistent between frames while real text is stable, so
        agreement is a far better signal than any single frame's confidence.
        """
        usable = [frame for frame in frames if frame]
        if not usable:
            return []
        if len(usable) == 1:
            return self.read_sync(usable[0])
        return merge_readings([self.read_sync(frame) for frame in usable])

    async def read_consensus(self, frames: list[bytes]) -> list[TextLine]:
        return await run_inference(self.read_consensus_sync, frames)

    def warmup(self) -> None:
        """The first call loads models. Pay for it at startup, not on stage."""
        if self._engine is None:
            return
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (64, 32), "white").save(buffer, "JPEG")
        self.read_sync(buffer.getvalue())
        log.info("OCR ready: %s", self.name)


def build_reader(preferred: str = "auto") -> TextReader:
    """First engine that loads wins. `preferred` pins one; "none" disables OCR."""
    if preferred == "none":
        return TextReader(None)

    names = list(_ENGINES) if preferred == "auto" else [preferred]
    for name in names:
        try:
            return TextReader(_ENGINES[name]())
        except Exception as exc:
            log.info("OCR engine %s unavailable: %s", name, exc)

    log.warning("No OCR engine loaded; text can only be read by the vision provider")
    return TextReader(None)


# --- Post-processing --------------------------------------------------------


def clean_lines(lines: Iterable[TextLine]) -> list[TextLine]:
    """Drop noise: empty or implausible text, low confidence, and duplicates."""
    kept: list[TextLine] = []
    for line in lines:
        text = " ".join(line.text.split())
        if not text or line.confidence < _MIN_CONFIDENCE:
            continue
        verdict = assess(text, line.confidence)
        if not verdict.keep:
            log.debug("OCR rejected %r: %s", text, verdict.reason)
            continue
        cleaned = replace(line, text=text)
        if any(_duplicate(cleaned, other) for other in kept):
            continue
        kept.append(cleaned)
    return kept


def _duplicate(a: TextLine, b: TextLine) -> bool:
    """Same text at nearly the same place: an engine reporting one line twice."""
    tolerance = max(a.height, b.height, _FALLBACK_ROW_TOLERANCE)
    return normalize(a.text) == normalize(b.text) and abs(a.top - b.top) <= tolerance


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


def merge_readings(readings: list[list[TextLine]]) -> list[TextLine]:
    """Combine several readings of the same scene.

    Agreement across frames is strong evidence, but not the only evidence:
    plausibility filtering already rejects texture noise outright, so a line
    seen once but clearly word-like is kept. Requiring agreement outright cost
    more real text than it saved.
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
                log.debug("OCR dropped %r: seen once, score %.2f", best.text, verdict.score)
                continue

        merged.append(
            TextLine(
                text=best.text,
                confidence=best.confidence,
                top=sum(line.top for line in group) / agreement,
                left=sum(line.left for line in group) / agreement,
                height=sum(line.height for line in group) / agreement,
                agreement=agreement,
            )
        )

    return merged


def reading_rows(lines: list[TextLine]) -> list[list[TextLine]]:
    """Group lines into rows, top to bottom; each row runs left to right.

    Sorting by vertical position alone zig-zags across a multi-column sign,
    because two side-by-side labels almost never share an exact position.
    """
    if not lines:
        return []

    ordered = sorted(lines, key=lambda line: line.center_y)
    rows: list[list[TextLine]] = [[ordered[0]]]
    for line in ordered[1:]:
        if _same_row(rows[-1][0], line):
            rows[-1].append(line)
        else:
            rows.append([line])
    return [sorted(row, key=lambda line: line.left) for row in rows]


def _same_row(a: TextLine, b: TextLine) -> bool:
    if a.height and b.height:
        return abs(a.center_y - b.center_y) <= 0.5 * min(a.height, b.height)
    return abs(a.top - b.top) <= _FALLBACK_ROW_TOLERANCE


def sort_reading_order(lines: list[TextLine]) -> list[TextLine]:
    return [line for row in reading_rows(lines) for line in row]


def format_for_speech(lines: list[TextLine]) -> str:
    """Rows become sentences so the TTS engine pauses between them; words on
    one row run together, as they would be read.

    Stray marks are voiced literally by a speech engine -- "EXIT comma comma"
    -- so each line is tidied before it is spoken.
    """
    parts: list[str] = []
    for row in reading_rows(lines):
        text = " ".join(clean_for_speech(line.text) for line in row)
        text = " ".join(text.split())
        if text:
            parts.append(text)

    if not parts:
        return NO_TEXT_FOUND
    return f"It reads: {'. '.join(parts)}."
