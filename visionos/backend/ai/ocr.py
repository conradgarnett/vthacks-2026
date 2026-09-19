"""Text reading.

Local first, on measurement: Apple's Vision framework reads a sign in ~15 ms
against roughly 1-2 s for an API round trip, needs no credentials, and keeps
working when the network dies. Claude remains the escalation path for what OCR
cannot do -- questions *about* text, handwriting, unusual layouts.

Two engines share one pipeline. Apple Vision on macOS; RapidOCR (PaddleOCR
models on ONNX Runtime, CPU) everywhere else. `build_reader` takes the first
that loads, so the same code reads text on a Mac and on a Windows laptop.
Everything below the engine -- preprocessing, tiling, consensus, dedupe,
reading order -- is shared and measured with `eval/`.

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
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from backend.ai.lexicon import correct_text
from backend.ai.medication import guard as medication_guard
from backend.ai.text_quality import assess, clean_for_speech, normalize

log = logging.getLogger(__name__)

NO_TEXT_FOUND = "I don't see any readable text."
OCR_UNAVAILABLE = "I can't read text right now."

# Vision's default is 1/32 (~0.031) and silently skips anything smaller, which
# is most signage seen across a room. 0.02 recovers that without the noise
# floor: below ~0.01 Vision starts finding "text" in textures.
_MIN_TEXT_HEIGHT = 0.02
_MIN_TEXT_HEIGHT_RETRY = 0.012
# Used only once a document has already been evidenced at the safe
# threshold. Recovery on real receipts plateaus here (43%); lower buys
# nothing and costs texture hallucination.
_MIN_TEXT_HEIGHT_DENSE = 0.008
# Lines at the safe threshold needed before the denser pass is allowed.
# One is enough, and is the whole point: textures produce nothing at the
# safe threshold, so any text at all means a text-bearing surface worth
# looking at harder. Measured 27% receipt recovery at 1, 21% at 3.
_DOCUMENT_LINE_HINT = 1

# Engine confidence is coarse. This only drops the obviously-bad; real
# filtering is linguistic.
_MIN_CONFIDENCE = 0.3

# Two lines within this fraction of frame height are one row, so a two-column
# sign reads left-to-right instead of zig-zagging down the page. Used only
# when an engine reports no line height; with heights, rows are grouped by
# the lines themselves, so a distant multi-line sign keeps its lines apart.
_SAME_LINE_TOLERANCE = 0.04

# Target longest edge after upscaling. More pixels per stroke measurably
# improves recall on distant text.
_UPSCALE_BELOW_PX = 1600
_MAX_UPSCALE = 3.0

# 2x2 tiles quarter the frame, so text at 1.2% of it becomes ~5% of a tile --
# from invisible to comfortably readable. Overlap keeps text that straddles a
# boundary whole in at least one tile.
# Tried in order, stopping as soon as a reading appears. 4x4 is the practical
# floor: finer tiles cut words into fragments faster than they reveal them.
_TILE_GRIDS = (2, 4)
_TILE_OVERLAP = 0.12
# Frames to tile when escalating. Tiling is the expensive path, so this is
# the accuracy/latency dial: 1 frame forfeits consensus, 3 blew the budget.
_TILE_FRAMES = 1
# Total characters below which a full-frame reading is treated as a scrap
# worth escalating on. Most real signage clears this easily.
_THIN_RESULT_CHARS = 6

# A line this long counts as a real reading, which licenses discarding
# scraps next to it. Below it, short lines are all we have. Set to 4 so
# that "EXIT" -- the single most important word on any sign -- counts,
# and the stray "il" beside it gets dropped.
_SUBSTANTIAL_LINE_CHARS = 4
# Lines shorter than this are scraps unless strongly plausible -- a room
# number like "B12" must survive beside a longer line.
_FRAGMENT_CHARS = 4
_FRAGMENT_RESCUE_SCORE = 0.45
# Fraction of a short line's characters that must appear as one run inside
# a longer line for it to count as a piece of that line rather than new
# text. Tuned so "ire Cxi" collapses into "Fire CXLE" while genuinely
# different words that happen to share a stem do not.
_FRAGMENT_OVERLAP = 0.7
# Reading strength below which the image itself is transformed and retried.
# Roughly 'one short plausible word or less'. Enhancement costs nine extra
# recognitions, so it stays reserved for reads that found almost nothing.
_ENHANCE_STRENGTH_FLOOR = 3.0
# An enhanced reading must carry at least this much plausible text to be
# accepted -- about one real word -- and beat the original by this margin.
_ENHANCE_ACCEPT_FLOOR = 2.2
_ENHANCE_MARGIN = 1.6


@dataclass(slots=True)
class TextLine:
    text: str
    confidence: float
    # Normalized, origin top-left (Vision's own origin is bottom-left).
    top: float
    left: float
    # Line height as a fraction of the frame; 0 when the engine gave none.
    height: float = 0.0
    # Frames this line was seen in. 1 unless it came through consensus.
    agreement: int = 1

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2.0


class TextReader:
    """The reading pipeline, minus the engine.

    Subclasses supply `_recognize`; everything else -- preprocessing, tiling,
    consensus, dedupe -- is shared, so a change measured on one engine with
    `eval/` applies to both. Instantiated directly it is a null reader that
    is never available, which is what the app gets with no engine installed.
    """

    name = "none"
    # Whether `_recognize` honours `minimum_height`. Vision skips text below
    # a fraction of the frame and is worth a second, lower pass; an engine
    # with no such floor would just repeat itself.
    has_height_floor = False
    # Whether a frame costs enough (seconds on a CPU engine) that a burst
    # should stop as soon as two frames agree. Vision reads a frame in 15 ms
    # and always takes the full vote.
    costly_frames = False

    @property
    def available(self) -> bool:
        return False

    def warmup(self) -> None:
        """First call loads the engine. Never pay it on stage."""
        if not self.available:
            return
        try:
            from PIL import Image

            buffer = io.BytesIO()
            Image.new("RGB", (64, 32), "white").save(buffer, "JPEG")
            self._recognize(buffer.getvalue(), _MIN_TEXT_HEIGHT)
            log.info("OCR ready: %s", self.name)
        except Exception:
            log.exception("OCR warmup failed")

    # -- single frame ------------------------------------------------------

    def read_sync(self, frame_jpeg: bytes) -> list[TextLine]:
        """Read one frame, escalating to tiles for small text.

        Vision's minimum text height is a *fraction of the frame*, so a sign
        across a room is invisible to it no matter how many pixels the sensor
        captured -- measured CER was 1.00 below 2% of frame height, total
        failure. Lowering the threshold instead makes it read texture as text.

        Tiling fixes the ratio rather than the threshold: text occupying 1.2%
        of the full frame occupies ~5% of a quarter tile, which is comfortably
        within range. The full-frame pass runs first because it is cheap and
        handles ordinary signage.
        """
        if not self.available or not frame_jpeg:
            return []

        try:
            prepared = _prepare(frame_jpeg)
            lines = self._read_full(prepared)
            if _needs_tiles(lines):
                lines = self._escalate_tiles(prepared, lines)
            if _needs_enhancement(lines):
                lines = self._escalate_enhanced(prepared, lines)
            return lines
        except Exception:
            # This sits in the user's speech path: degrade to "no text", never
            # take the session down with a traceback.
            log.exception("OCR failed; treating the frame as having no text")
            return []

    def _escalate_enhanced(
        self, prepared: bytes, lines: list[TextLine]
    ) -> list[TextLine]:
        """Last resort: re-read transformed copies of the image.

        Cursive faces return *nothing* rather than garbage -- Great Vibes,
        Pacifico and Parisienne produce no observations at all -- so there is
        nothing for post-processing to repair. Changing the image is the only
        remaining lever: shearing the slant upright, thickening thin connected
        strokes, binarizing low local contrast, flattening a curved label.

        Measured on 31 hard cursive and handwriting samples, 24 improved and 5
        went from unreadable to correct.

        Selection cannot use ground truth, so the most plausible result wins
        and it must beat what we already had. Every variant is a guess, and a
        guess that reads confidently is the dangerous kind.
        """
        from backend.ai.enhance import variants

        best = lines
        best_score = _reading_strength(lines)

        # A transformed image must be decisively better, not marginally.
        # Accepting any improvement tripled hallucination on symbol-only
        # images (1/12 -> 3/12): enhancing a barcode yields weak junk that
        # still beats nothing. Requiring a real word's worth of plausible
        # text, and a clear margin over the original, keeps the gain without
        # the invention.
        for name, image in variants(prepared):
            candidate = self._read_full(image)
            score = _reading_strength(candidate)
            if score < _ENHANCE_ACCEPT_FLOOR or not _contains_a_word(candidate):
                continue
            if score > max(best_score * _ENHANCE_MARGIN, _ENHANCE_ACCEPT_FLOOR):
                best, best_score = candidate, score
                log.debug("enhance: %s improved the read", name)

        return best

    def _read_full(self, prepared: bytes) -> list[TextLine]:
        """Whole-frame pass, with a denser second look when a document appears.

        Minimum text height cannot be one global value. At 0.02 a receipt
        loses almost everything -- 9% of its lines -- because 44 lines on one
        page means each is about 2% of frame height. At 0.008 it recovers 43%,
        but open scenes then read carpet, brick and foliage as words
        (hallucination 0/8 -> 4/8).

        The safe pass settles which case this is. Textures produce *nothing*
        at 0.02, so they can never reach the denser pass; a label or receipt
        produces several lines and does. Evidence, not a guess about intent.

        An engine with no frame-relative floor reads every size in its one
        pass, so the retry and the denser look would only repeat it.
        """
        lines = self._recognize(prepared, _MIN_TEXT_HEIGHT)
        if not self.has_height_floor:
            return lines
        if not lines:
            return self._recognize(prepared, _MIN_TEXT_HEIGHT_RETRY)

        if len(lines) >= _DOCUMENT_LINE_HINT:
            denser = self._recognize(prepared, _MIN_TEXT_HEIGHT_DENSE)
            if len(denser) > len(lines):
                return denser
        return lines

    def _escalate_tiles(self, prepared: bytes, lines: list[TextLine]) -> list[TextLine]:
        """Walk finer grids until something readable appears.

        Tiles rescue small text but damage large text: a crop cuts words at
        its boundary and re-reads what the full frame already got right, so a
        good reading acquires a garbled twin ("Departures" became
        "DLpartiirL Departures"). Measured, unconditional tiling made 3.5%-14%
        text worse while helping only below 2% -- hence the escalation gate.
        """
        for grid in _TILE_GRIDS:
            lines = _dedupe(lines + self._read_tiles(prepared, grid))
            if not _needs_tiles(lines):
                break
        return lines

    def _read_tiles(self, frame_jpeg: bytes, grid: int = 2) -> list[TextLine]:
        """OCR overlapping crops, mapping results back to frame coordinates.

        Tiles overlap so text straddling a boundary is whole in at least one
        of them; without the overlap a centred sign is reliably cut in half.
        """
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(frame_jpeg))
            image.load()
        except Exception:
            return []

        width, height = image.size
        found: list[TextLine] = []
        step = 1.0 / grid

        for row in range(grid):
            for col in range(grid):
                left = max(0.0, col * step - _TILE_OVERLAP)
                top = max(0.0, row * step - _TILE_OVERLAP)
                right = min(1.0, (col + 1) * step + _TILE_OVERLAP)
                bottom = min(1.0, (row + 1) * step + _TILE_OVERLAP)

                box = (
                    int(left * width), int(top * height),
                    int(right * width), int(bottom * height),
                )
                tile = image.crop(box)
                if min(tile.size) < 40:
                    continue

                # Upscale so the crop carries enough pixels per stroke.
                factor = min(_MAX_UPSCALE, _UPSCALE_BELOW_PX / max(tile.size))
                if factor > 1.05:
                    tile = tile.resize(
                        (int(tile.width * factor), int(tile.height * factor)),
                        Image.LANCZOS,
                    )

                buffer = io.BytesIO()
                tile.save(buffer, "JPEG", quality=94)
                for line in self._recognize(buffer.getvalue(), _MIN_TEXT_HEIGHT):
                    # Tile-relative coordinates back to frame-relative.
                    found.append(
                        TextLine(
                            text=line.text,
                            confidence=line.confidence,
                            top=top + line.top * (bottom - top),
                            left=left + line.left * (right - left),
                            height=line.height * (bottom - top),
                        )
                    )

        return found

    async def read(self, frame_jpeg: bytes) -> list[TextLine]:
        from backend.perception.runtime import run_ocr

        return await run_ocr(self.read_sync, frame_jpeg)

    # -- background peeks --------------------------------------------------

    def read_quick_sync(self, frame_jpeg: bytes) -> list[TextLine]:
        """One whole-frame pass and nothing more: no tiles, no transforms.

        For a background peek at what is in view, where the point is to be
        cheap and current rather than thorough. The escalations cost seconds
        on a CPU engine and are reserved for a read the user asked for.
        """
        if not self.available or not frame_jpeg:
            return []
        try:
            return self._read_full(_prepare(frame_jpeg))
        except Exception:
            log.exception("OCR peek failed; treating the frame as having no text")
            return []

    async def read_quick(self, frame_jpeg: bytes) -> list[TextLine]:
        from backend.perception.runtime import run_ocr

        return await run_ocr(self.read_quick_sync, frame_jpeg)

    def combine_readings(self, readings: list[list[TextLine]]) -> list[TextLine]:
        """Consensus over readings taken one frame at a time, as a burst gets.

        Peeks arrive seconds apart rather than milliseconds, but agreement
        across them means the same thing: text that survived a different
        blur and glare each time is real, and the medication guard's
        corroboration counts it the same way.
        """
        usable = [reading for reading in readings if reading]
        if not usable:
            return []
        if len(usable) == 1:
            return [line for line in usable[0] if _keep(line)]
        return _merge(usable)

    # -- multi frame -------------------------------------------------------

    def read_consensus_sync(self, frames: list[bytes]) -> list[TextLine]:
        """Read several frames and reconcile them.

        OCR noise is inconsistent between frames while real text is stable, so
        agreement is a far better signal than any single frame's confidence.

        Tiling is deliberately not done per frame. It is the expensive path --
        up to 21 recognitions per frame -- and tiling all three blew the
        latency budget (p50 1.1 s, tail 3.7 s against 2.5 s). Since tiles only
        matter for text too small to survive blur anyway, they run once, on
        the sharpest frame.
        """
        usable = [f for f in frames if f]
        if not usable or not self.available:
            return []
        try:
            return self._read_consensus(usable)
        except Exception:
            log.exception("OCR failed; treating the burst as having no text")
            return []

    def _read_consensus(self, usable: list[bytes]) -> list[TextLine]:
        prepared = [_prepare(frame) for frame in usable]
        readings: list[list[TextLine]] = []
        for frame in prepared:
            readings.append(self._read_full(frame))
            # On an engine that takes seconds per frame, two identical
            # readings are already the agreement the third frame would buy.
            if self.costly_frames and len(readings) >= 2 and _same_reading(readings[-1], readings[-2]):
                break
        merged = _merge(readings) if len(readings) > 1 else [
            line for line in readings[0] if _keep(line)
        ]

        if _needs_tiles(merged):
            # Two frames, not one: tiling a single frame cost accuracy on the
            # smallest text (CER 0.70 -> 0.85) because it forfeited consensus
            # exactly where readings are least reliable. Two keeps the vote
            # and still lands well inside the latency budget.
            sharpest = sorted(prepared, key=_sharpness, reverse=True)[:_TILE_FRAMES]
            tiled: list[TextLine] = []
            for frame in sharpest:
                tiled.extend(self._escalate_tiles(frame, []))

            # Union of the best variants, not a consensus vote. Text this
            # small garbles differently in every frame, so demanding agreement
            # rejects the only readings available and the user hears nothing.
            # This is already the last resort; plausibility is the gate here.
            merged = _dedupe(merged + tiled)

        # Cursive returns nothing rather than garbage, so tiles cannot help:
        # there is no text to cut up. Transforming the image is the only
        # remaining lever, and it runs last because it is the most expensive.
        if _needs_enhancement(merged):
            sharpest = max(prepared, key=_sharpness)
            merged = self._escalate_enhanced(sharpest, merged)

        return merged

    async def read_consensus(self, frames: list[bytes]) -> list[TextLine]:
        from backend.perception.runtime import run_ocr

        return await run_ocr(self.read_consensus_sync, frames)

    # -- engine ------------------------------------------------------------

    def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
        """Raw engine call: lines in frame-normalized coordinates, already
        filtered through `_accept`. Subclasses override this and nothing else."""
        return []


def _accept(text: str, confidence: float) -> bool:
    """The gate every engine's output passes through before it is a line."""
    if confidence < _MIN_CONFIDENCE:
        return False
    verdict = assess(text, confidence)
    if not verdict.keep:
        log.debug("OCR rejected %r: %s", text, verdict.reason)
    return verdict.keep


class AppleVisionOCR(TextReader):
    """macOS-native text recognition. No download, no network, no key."""

    name = "apple-vision"
    has_height_floor = True

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
            # Top candidate only. Two variants of using Vision's alternatives
            # were measured and both were worse than ignoring them:
            #   score all, take most plausible -> CER 0.199 (from 0.182). The
            #     linguistic scorer promotes plausible-but-wrong readings over
            #     Vision's correct first guess.
            #   use alternatives only to rescue a rejected top candidate ->
            #     also 0.199, because it revives lines that were rightly
            #     dropped, adding garbage at large text sizes.
            # Vision's own ranking beats the heuristic at choosing among its
            # candidates; the heuristic is only better at deciding whether to
            # speak at all. Don't re-litigate this without running eval/.
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            confidence = float(candidate.confidence())
            text = str(candidate.string()).strip()
            if not _accept(text, confidence):
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
                    height=box.size.height,
                )
            )

        return lines


# RapidOCR's detector scales with pixel count and runs on the CPU. On a
# 1920x1080 frame one pass took 3-4 s on a laptop; at this cap it is well
# under a second. Detection only: recognition crops are cut from the
# full-resolution frame, because recognizing downscaled crops lost the
# spaces between words ("CarParkLevel3") and cost 8 points of exact match.
_RAPID_DET_MAX_SIDE_PX = 1280


class RapidOCR(TextReader):
    """Cross-platform OCR: PaddleOCR detection and recognition on ONNX Runtime.

    CPU only, no credentials. It has no frame-relative minimum text height,
    so `minimum_height` is ignored; small text still benefits from the shared
    tiling path.
    """

    name = "rapidocr"
    costly_frames = True

    def __init__(self) -> None:
        self._engine = None
        try:
            try:
                from rapidocr import RapidOCR as Engine  # 2.x
            except ImportError:
                from rapidocr_onnxruntime import RapidOCR as Engine  # 1.x
            self._engine = Engine()
        except Exception as exc:
            log.info("RapidOCR unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self._engine is not None

    def _detect_small_recognize_full(self, image) -> list[tuple]:
        """(box, text, score) with boxes in full-frame pixel coordinates.

        The detector sees a copy no larger than _RAPID_DET_MAX_SIDE_PX on its
        longest side; its boxes are scaled back up and the recognizer reads
        crops cut from the original. Falls back to the engine's own single
        pass if its internals are not the ones this was written against.
        """
        import cv2
        import numpy as np

        engine = self._engine
        longest = max(image.shape[:2])
        scale = min(1.0, _RAPID_DET_MAX_SIDE_PX / longest)
        small = (
            cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if scale < 1.0
            else image
        )

        try:
            boxes_small, _ = engine(small, use_cls=False, use_rec=False)
            if not boxes_small:
                return []
            boxes = [np.asarray(box, dtype=np.float32) / scale for box in boxes_small]
            crops = engine.get_crop_img_list(image, boxes)
            if getattr(engine, "use_cls", False):
                crops, _, _ = engine.text_cls(crops)
            recognized, _ = engine.text_rec(crops)
            floor = float(getattr(engine, "text_score", 0.5))
            return [
                (box, result[0], float(result[1]))
                for box, result in zip(boxes, recognized)
                if float(result[1]) >= floor
            ]
        except (AttributeError, TypeError, ValueError):
            log.debug("RapidOCR internals differ; using its single pass at reduced size")
            return [
                (np.asarray(box, dtype=np.float32) / scale, text, score)
                for box, text, score in _rapid_results(engine(small))
            ]

    def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return []
        height, width = image.shape[:2]

        lines: list[TextLine] = []
        for box, text, score in self._detect_small_recognize_full(image):
            text = _fix_digit_confusions(_split_letter_digit_runs(str(text).strip()))
            if not _accept(text, score):
                continue
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            lines.append(
                TextLine(
                    text=text,
                    confidence=score,
                    top=min(ys) / height,
                    left=min(xs) / width,
                    height=(max(ys) - min(ys)) / height,
                )
            )
        return lines


# Bold capitals lose their spaces on this recognizer, and a number glued to
# the words around it is unreadable to anything downstream: "TAKE1CAPSULE
# EVERY8 HOURS" matches no dose pattern, and the digit fix-up below then
# turned the 1 into an I, so the dose on a prescription label was never even
# a candidate. A digit run between runs of three or more letters is a
# separate token. Codes keep their shape: "204B", "B12", "R2D2" and "Rx6233425"
# have no three-letter run beside the digits.
_LETTER_DIGIT_BOUNDARY = re.compile(r"(?<=[A-Za-z]{3})(?=\d)|(?<=\d)(?=[A-Za-z]{3})")


def _split_letter_digit_runs(text: str) -> str:
    return _LETTER_DIGIT_BOUNDARY.sub(" ", text)


# The recognizer's classic confusions, seen on clean Arial and Candara:
# "Room" -> "Ro0m", "EXIT" -> "EX1T". Only a lone digit inside a run of
# letters is touched, so room numbers, gate codes and "204B" pass through.
_DIGIT_CONFUSIONS = {"0": ("o", "O"), "1": ("l", "I"), "5": ("s", "S")}


def _fix_digit_confusions(text: str) -> str:
    def fix_token(token: str) -> str:
        letters = sum(ch.isalpha() for ch in token)
        digits = sum(ch.isdigit() for ch in token)
        if letters < 3 or digits != 1:
            return token
        chars = list(token)
        for index in range(1, len(chars) - 1):
            ch = chars[index]
            if ch in _DIGIT_CONFUSIONS and chars[index - 1].isalpha() and chars[index + 1].isalpha():
                lower, upper = _DIGIT_CONFUSIONS[ch]
                both_upper = chars[index - 1].isupper() and chars[index + 1].isupper()
                chars[index] = upper if both_upper else lower
        return "".join(chars)

    return " ".join(fix_token(token) for token in text.split(" "))


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


_ENGINES: dict[str, type[TextReader]] = {
    AppleVisionOCR.name: AppleVisionOCR,
    RapidOCR.name: RapidOCR,
}


def build_reader(preferred: str = "auto") -> TextReader:
    """First engine that loads wins. `preferred` pins one; "none" disables OCR."""
    if preferred == "none":
        return TextReader()

    names = list(_ENGINES) if preferred == "auto" else [preferred]
    for name in names:
        engine_type = _ENGINES.get(name)
        if engine_type is None:
            log.warning("Unknown OCR engine %r", name)
            continue
        reader = engine_type()
        if reader.available:
            return reader

    log.warning("No OCR engine loaded; text can only be read by the vision provider")
    return TextReader()


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


def _same_reading(a: list[TextLine], b: list[TextLine]) -> bool:
    """Two frames read the same text, ignoring order, case and punctuation."""
    if not a or not b:
        return False
    return sorted(normalize(l.text) for l in a) == sorted(normalize(l.text) for l in b)


def _sharpness(frame_jpeg: bytes) -> float:
    """Focus estimate: variance of a Laplacian, higher is sharper.

    Small text is the only case that reaches tiling, and it does not survive
    motion blur, so spending the expensive pass on the sharpest frame of the
    burst rather than an arbitrary one is close to free accuracy.
    """
    try:
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(frame_jpeg)).convert("L")
        # Downscale first: sharpness ranking is unaffected and this keeps the
        # metric far cheaper than the recognition it is choosing between.
        image.thumbnail((480, 480))
        pixels = np.asarray(image, dtype=np.float32)

        laplacian = (
            -4 * pixels[1:-1, 1:-1]
            + pixels[:-2, 1:-1] + pixels[2:, 1:-1]
            + pixels[1:-1, :-2] + pixels[1:-1, 2:]
        )
        return float(laplacian.var())
    except Exception:
        return 0.0


def _contains_a_word(lines: list[TextLine]) -> bool:
    """Does this reading contain something that is actually a word?

    The discriminator between a rescued cursive read and an enhanced barcode.
    Transforming a barcode yields strings like "Ip h.jlil" -- plausible enough
    by shape to score, but no token is a word. A rescued script face yields
    "reception" or "DIET COLA", which are.
    """
    from backend.ai.lexicon import LEXICON

    for line in lines:
        for token in line.text.split():
            cleaned = "".join(ch for ch in token if ch.isalpha()).lower()
            if len(cleaned) >= 4 and (
                cleaned in LEXICON or correct_text(cleaned).lower() in LEXICON
            ):
                return True
    return False


def _reading_strength(lines: list[TextLine]) -> float:
    """How much real text a reading appears to contain.

    Plausibility times length, so a long confident line outranks a short one
    and a scrap of nonsense outranks nothing only barely. Used to choose
    between image variants at runtime, where no ground truth exists.
    """
    return sum(_plausibility(line) * len(line.text.strip()) for line in lines)


def _needs_enhancement(lines: list[TextLine]) -> bool:
    """Did the ordinary path fail badly enough to justify transforming the image?

    Enhancement costs nine extra recognitions, so it is reserved for reads
    that produced almost nothing -- which is exactly what cursive does.
    """
    return _reading_strength(lines) < _ENHANCE_STRENGTH_FLOOR


def _needs_tiles(lines: list[TextLine]) -> bool:
    """Was the full-frame pass good enough to skip the tile escalation?

    Nothing found, or only a scrap, means the text was probably too small for
    Vision's frame-relative minimum height. A solid reading means tiling can
    only add garbled duplicates.
    """
    if not lines:
        return True
    return sum(len(line.text.strip()) for line in lines) < _THIN_RESULT_CHARS


def _plausibility(line: TextLine) -> float:
    return assess(line.text, line.confidence).score


def _dedupe(lines: list[TextLine]) -> list[TextLine]:
    """Keep one reading per physical piece of text, the most plausible one.

    Position is the primary key, not text similarity. Two bad readings of one
    word garble differently -- "DLpartiirL" and "Departures" score only ~0.6
    similar -- so text matching emitted both and the user heard the correct
    reading with a nonsense twin attached. The same text cannot be in two
    places at once, so overlapping boxes are the same text by definition.

    Ordering by plausibility rather than Vision's confidence matters: Vision
    reports ~0.5 for nearly everything, which makes confidence close to a
    coin flip for choosing between variants.
    """
    kept: list[TextLine] = []
    for line in sorted(lines, key=lambda l: (-_plausibility(l), -len(l.text))):
        key = normalize(line.text)
        if not key:
            continue

        duplicate = False
        for other in kept:
            # Position alone decides. Text similarity used to force a dedupe
            # on its own, which collapsed genuinely repeated signage -- "PUSH"
            # on two different doors became one "PUSH". Two readings are the
            # same physical text only if they are in the same physical place;
            # identical words elsewhere in the frame are different words.
            if (
                abs(line.top - other.top) < _SAME_POSITION_TOP
                and abs(line.left - other.left) < _SAME_POSITION_LEFT
            ):
                # Same place is the rule; clearly different words are the
                # exception. On a rotated or curved label a wide line's
                # bounding box is tall enough to swallow the line beneath
                # it, and position alone then discarded the drug name on a
                # prescription label in favour of the Rx number under it:
                # 0/10 drug names reached speech, 5/10 with this.
                if _clearly_different_words(key, normalize(other.text)):
                    continue
                duplicate = True
                break

        if not duplicate:
            kept.append(line)
    return kept


# Two readings this similar are the same line read imperfectly twice. Exact
# matching fails here: degraded frames disagree on characters ("204B" vs
# "2048") while clearly referring to the same text.
_SAME_LINE_SIMILARITY = 0.72

# Two readings this close together are the same physical text, however
# differently they were garbled.
#
# The horizontal tolerance is tight on purpose. It was 0.20, which treated two
# words on the same row as one place: an engine that boxes "Room" and "204B"
# separately had one of them silently dropped. Words sit closer together
# vertically than horizontally, so the top tolerance can stay loose enough to
# absorb tile-versus-full-frame disagreement about where a line begins.
_SAME_POSITION_TOP = 0.05
_SAME_POSITION_LEFT = 0.06

# Two readings in the same place that share no run of characters are not
# one text garbled twice; they are two texts whose boxes overlap. The
# garbled twins the position rule exists for ("Departures" against
# "DLpartiirL") score about 0.6, far above this floor, and short scraps
# never qualify, so "il" beside "EXIT" still collapses.
_DIFFERENT_WORDS_RATIO = 0.3
_DIFFERENT_WORDS_MIN_CHARS = 4
_DIFFERENT_WORDS_MAX_RUN = 2


def _clearly_different_words(a: str, b: str) -> bool:
    if len(a) < _DIFFERENT_WORDS_MIN_CHARS or len(b) < _DIFFERENT_WORDS_MIN_CHARS:
        return False
    if _longest_common_run(a, b) > _DIFFERENT_WORDS_MAX_RUN:
        return False
    return SequenceMatcher(None, a, b).ratio() < _DIFFERENT_WORDS_RATIO


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
        # Plausibility first: Vision reports ~0.5 confidence for nearly
        # everything, so confidence alone barely discriminates between
        # "Departures" and "DLpartiirL".
        best = max(group, key=lambda line: (_plausibility(line), len(line.text)))

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
                height=sum(line.height for line in group) / agreement,
                agreement=agreement,
            )
        )

    # Frames can garble the same word differently enough to land in separate
    # groups; position catches what text similarity missed.
    return _dedupe(merged)


def _longest_common_run(a: str, b: str) -> int:
    """Length of the longest substring shared by a and b."""
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        current = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def _drop_overlapping_fragments(lines: list[TextLine]) -> list[TextLine]:
    """Remove pieces of a word that a longer line already contains.

    Hard typefaces make the full-frame pass return little, which triggers tile
    escalation, and tiles then cut the word apart. Observed on script and
    decorative faces: "Fire Exit" came back as "Fire CXLE  ire Cxi", and
    "Reception" as "ion  Rec  on" -- the same word spoken two or three times
    in pieces.

    Position dedupe cannot see this: the fragments sit in different tiles and
    disagree on characters, so they are neither the same place nor similar
    text. What identifies them is that a short line's characters are mostly a
    run inside a longer one.
    """
    if len(lines) < 2:
        return lines

    ordered = sorted(lines, key=lambda l: -len(l.text.strip()))
    kept: list[TextLine] = []

    for line in ordered:
        key = normalize(line.text)
        if not key:
            continue
        contained = any(
            _longest_common_run(key, normalize(other.text)) / len(key)
            >= _FRAGMENT_OVERLAP
            for other in kept
        )
        if not contained:
            kept.append(line)

    return kept


def _drop_fragments(lines: list[TextLine]) -> list[TextLine]:
    """Discard scraps sitting beside a substantial reading.

    A sign's border, a reflection or a background edge routinely yields a
    two-or-three character fragment alongside the real text -- observed output
    included "J 44 Elevator" and "3Ji 11 li4 1 Rectrplion rr g". The fragment
    is spoken with the same confidence as the word, and the listener has no
    way to tell which part was real.

    Only applied when something substantial was found: if every line is short,
    the short lines are all we have and a room number is worth speaking.
    """
    if len(lines) < 2:
        return lines

    longest = max(len(line.text.strip()) for line in lines)
    if longest < _SUBSTANTIAL_LINE_CHARS:
        return lines

    return [
        line
        for line in lines
        if len(line.text.strip()) >= _FRAGMENT_CHARS
        or _plausibility(line) >= _FRAGMENT_RESCUE_SCORE
    ]


# -- reading order and speech ------------------------------------------------


def reading_rows(lines: list[TextLine]) -> list[list[TextLine]]:
    """Group lines into rows, top to bottom; each row runs left to right.

    Sorting by vertical position alone zig-zags across a multi-column sign,
    because two side-by-side labels almost never share an exact position.
    Rows are grouped by the lines' own heights when the engine reports them:
    a fixed fraction of the frame collapsed a distant three-line sign into
    one row and read it left-to-right as gibberish.
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
    return abs(a.top - b.top) <= _SAME_LINE_TOLERANCE


def sort_reading_order(lines: list[TextLine]) -> list[TextLine]:
    """Top to bottom, then left to right within a row."""
    return [line for row in reading_rows(lines) for line in row]


def format_for_speech(lines: list[TextLine]) -> str:
    """Render recognized text as something worth hearing.

    Rows are joined with periods so the TTS engine pauses between them; a
    sign run together as one breath is hard to follow. Words an engine split
    within one row run together, as they would be read.

    Stray marks are voiced literally by a speech engine -- "EXIT comma
    comma" -- so they are stripped rather than passed through.
    """
    parts: list[str] = []
    for row in reading_rows(_drop_overlapping_fragments(_drop_fragments(lines))):
        # Near-miss correction last, on text that has already survived every
        # filter. Cursive drops the lead-in capital, leaving a word one edit
        # from correct; the lexicon restores only that, and only for wording
        # it already knows.
        text = " ".join(correct_text(clean_for_speech(line.text)) for line in row)
        text = " ".join(text.split())
        if text:
            parts.append(text)

    if not parts:
        return NO_TEXT_FOUND
    spoken = f"It reads: {'. '.join(parts)}."
    # Medication labels are the one case where a confident number can cause
    # harm, so an uncorroborated dose is withheld rather than spoken.
    return medication_guard(spoken, lines)
