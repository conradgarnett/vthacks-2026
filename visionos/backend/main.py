"""FastAPI app: frames up over WebSocket, speech events down.

Wire protocol, one phone per socket:

  binary  JPEG                         live frame for perception (small, frequent)
  binary  b"READ" + (u32 len + JPEG)*  a burst of detailed frames to read text from
  binary  b"PEEK" + u32 len + JPEG     one detailed frame to read in the background
  binary  b"SCAN" + (u32 len + JPEG)*  two detailed frames to describe the scene from
  text    {"type": "scan"}             describe the room from the latest live frame
  text    {"type": "read"}             read text from the latest live frame
  text    {"type": "ask", "text": "..."}
  text    {"type": "locate", "text": "..."}   start an audio beacon
  text    {"type": "stop_beacon"}

Read frames arrive in one tagged message so they never touch the perception
pipeline: a full-resolution frame run through the tracker breaks every box
association made on the small live frames and fills the scene model with
duplicates. A burst rather than one frame because hand-held capture blurs and
glares differently each time, and OCR keeps only what the frames agree on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.ai.medication import UNREADABLE_DOSE
from backend.ai.ocr import (
    NO_TEXT_FOUND,
    TextReader,
    _sharpness,
    all_lines_sure,
    build_reader,
    format_for_speech,
)
from backend.ai.prompts import PROMPT_VERSION, READ_PROMPT, SCAN_PROMPT, scene_context
from backend.perception.pipeline import _decode_jpeg
from backend.scene.inference import Seen, describe_scan, inventory_sentence, refine_labels
from backend.ai.vision import VisionProvider, build_provider
from backend.config import get_settings
from backend.hazards.engine import HazardEngine
from backend.perception.pipeline import PerceptionPipeline
from backend.scene.queries import find_object
from backend.speech.chunker import SentenceChunker
from backend.telemetry import LatencyTrace, metrics_snapshot

log = logging.getLogger(__name__)

READ_TAG = b"READ"
PEEK_TAG = b"PEEK"
SCAN_TAG = b"SCAN"

# A scan is its own two-frame burst, detected at 1280 px instead of the live
# loop's 640: a chair six metres down a hallway is 25 px tall at 640 and was
# never seen. Something in both frames counts; something in one frame is
# only ever a hedged guess, and only when the detector was sure of it.
SCAN_FRAMES = 2
SCAN_IMGSZ = 1280
SCAN_MATCH_IOU = 0.2
SCAN_MATCH_AZIMUTH_DEG = 15.0

# Background reading. The client peeks at what is in view every few seconds
# and the reader keeps the last few results, so a Read can answer from what
# it already knows instead of making the user wait for a burst. Readings are
# forgotten after PEEK_KEEP_S and never more than PEEK_KEEP_MAX are held,
# which keeps the cache to a few kilobytes; a Read trusts only readings
# younger than PEEK_FRESH_S, since a label the user has put down is not the
# current moment. A soft frame is not worth the pass.
PEEK_KEEP_S = 8.0
PEEK_KEEP_MAX = 4
PEEK_FRESH_S = 6.0
PEEK_MIN_SHARPNESS = 100.0
# Three is the useful minimum for majority agreement; more costs latency the
# read budget cannot spare.
OCR_CONSENSUS_FRAMES = 3
# Total characters below which a local read is treated as a failure worth
# escalating to the vision model. Real signage and packaging clear this.
WEAK_READ_CHARS = 5

# A read that found nothing on a soft picture gets advice, not just the
# verdict. A webcam on a pair of glasses cannot focus on a label held
# against it, and the user cannot see that the picture is blurred. The
# floor is the reader's own sharpness score: a rendered label reads at
# 450 and up, fails from about 135 (2.5 px of blur) down, and a blank wall
# scores 0, so the advice is worded for the case where something is
# being held up and costs nothing when nothing is.
BLURRY_SHARPNESS = 100.0
BLURRY_HINT = (
    "If you are holding something up to read, try it about a hand's length "
    "from the camera and hold still."
)
NO_CAMERA = "I'm not receiving the camera yet."


def lan_address() -> str | None:
    """This machine's address on the local network, for the phone to open.

    Changes with the network, so it is looked up per request rather than
    once at startup; a laptop that moves from a guest Wi-Fi to a hotspot
    should show the new address on the next reload.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
        return None if address.startswith("127.") else address
    except OSError:
        return None


def read_is_weak(lines) -> bool:
    """Is a local read poor enough that a vision model should try instead?

    Empty is obviously weak. So is a handful of characters: a real sign or
    label carries more than a scrap, and a scrap is what a hard typeface
    produces right before it produces nonsense.
    """
    if not lines:
        return True
    return sum(len(line.text.strip()) for line in lines) < WEAK_READ_CHARS


# Naming what the text sits on. The read burst never enters perception, but
# the live loop was tracking the scene until the moment before, so the
# object in front of the camera is known. It is named only when the tracker
# is sure of it (the same 80% floor as every other guess here) and it sits
# near the middle of the view, where a label being read has to be. Living
# things are never what the text is on.
TEXT_HOLDER_MIN_CONFIDENCE = 0.8
TEXT_HOLDER_MAX_AZIMUTH_DEG = 25.0
NOT_A_SURFACE = frozenset({"person", "people", "dog", "cat", "face", "hand"})


def text_holder(objects) -> str | None:
    """The label of the object the text is most likely on, or None."""
    candidates = [
        o for o in objects
        if o.visible
        and o.confidence >= TEXT_HOLDER_MIN_CONFIDENCE
        and abs(o.azimuth_deg) <= TEXT_HOLDER_MAX_AZIMUTH_DEG
        and o.label not in NOT_A_SURFACE
    ]
    if not candidates:
        return None
    # The nearest thing in the middle of the view; an unknown distance
    # sorts last, and ties go to the most central.
    best = min(
        candidates,
        key=lambda o: (o.distance_m if o.distance_m is not None else 99.0, abs(o.azimuth_deg)),
    )
    return best.label


def with_holder(spoken: str, holder: str | None) -> str:
    """'It reads: ...' becomes 'On the bottle, it reads: ...'."""
    prefix = "It reads: "
    if not holder or not spoken.startswith(prefix):
        return spoken
    return f"On the {holder}, it reads: {spoken[len(prefix):]}"


def match_scan_frames(per_frame: list[list], frame_size: tuple[int, int]) -> tuple[list[Seen], list[Seen]]:
    """Sightings seen in both scan frames, and those seen in only one.

    Matched by label and box overlap; the frames are a fraction of a second
    apart, so a real thing barely moves. Confidence is the better of the
    two, position the average.
    """
    width, height = frame_size

    def norm(box) -> tuple[float, float, float, float]:
        return (box.x1 / width, box.y1 / height, box.x2 / width, box.y2 / height)

    if not per_frame:
        return [], []
    if len(per_frame) == 1:
        return [], [Seen(d.label, d.confidence, d.azimuth_deg, d.distance_m, 1, norm(d.box)) for d in per_frame[0]]

    first, second = per_frame[0], per_frame[1]
    claimed: set[int] = set()
    both: list[Seen] = []
    once: list[Seen] = []
    for a in first:
        best, best_iou = None, 0.0
        for index, b in enumerate(second):
            if index in claimed or b.label != a.label:
                continue
            iou = a.box.iou(b.box)
            if iou > best_iou:
                best, best_iou = index, iou
        if best is not None and best_iou >= SCAN_MATCH_IOU:
            claimed.add(best)
            b = second[best]
            both.append(Seen(
                a.label, max(a.confidence, b.confidence),
                (a.azimuth_deg + b.azimuth_deg) / 2,
                None if a.distance_m is None or b.distance_m is None else (a.distance_m + b.distance_m) / 2,
                2, norm(a.box),
            ))
        else:
            once.append(Seen(a.label, a.confidence, a.azimuth_deg, a.distance_m, 1, norm(a.box)))
    for index, b in enumerate(second):
        if index not in claimed:
            once.append(Seen(b.label, b.confidence, b.azimuth_deg, b.distance_m, 1, norm(b.box)))
    return both, once


def add_tracked(seen: list[Seen], objects) -> list[Seen]:
    """Objects the live loop has already confirmed count as seen, unless the
    scan already has that label in that direction."""
    out = list(seen)
    for o in objects:
        if not o.visible:
            continue
        if any(s.label == o.label and abs(s.azimuth_deg - o.azimuth_deg) <= SCAN_MATCH_AZIMUTH_DEG for s in out):
            continue
        out.append(Seen(o.label, o.confidence, o.azimuth_deg, o.distance_m, 2, None))
    return out


def detection_items(detections, frame_size) -> list[dict]:
    """Detections as the client draws them: label, confidence and a box
    normalized to the frame, origin top-left. A sighted helper checking
    the glasses sees what the detector believes; nothing here is spoken."""
    if not frame_size:
        return []
    width, height = frame_size
    return [
        {
            "label": d.label,
            "confidence": round(d.confidence, 2),
            "box": [
                round(d.box.x1 / width, 4), round(d.box.y1 / height, 4),
                round(d.box.x2 / width, 4), round(d.box.y2 / height, 4),
            ],
        }
        for d in detections
    ]


def looks_blurry(frames: list[bytes], sharpest: float | None = None) -> bool:
    """Was every frame of the burst too soft to carry text? `sharpest` is
    the burst's best sharpness score when the caller already has it."""
    if not frames:
        return False
    score = max(_sharpness(frame) for frame in frames) if sharpest is None else sharpest
    return score < BLURRY_SHARPNESS


def no_text_response(frames: list[bytes], sharpest: float | None = None) -> str:
    if looks_blurry(frames, sharpest):
        return f"{NO_TEXT_FOUND} {BLURRY_HINT}"
    return NO_TEXT_FOUND


def pack_read_frames(frames: list[bytes]) -> bytes:
    """READ tag, then each frame as a big-endian u32 length plus JPEG bytes.

    Mirrors sendReadFrames() in client/src/ws.ts.
    """
    return READ_TAG + b"".join(len(frame).to_bytes(4, "big") + frame for frame in frames)


def unpack_tagged_frames(payload: bytes, tag: bytes) -> list[bytes]:
    """Frames after a tag, each a big-endian u32 length plus JPEG bytes.
    A truncated tail is dropped, not guessed at."""
    frames: list[bytes] = []
    offset = len(tag)
    while offset + 4 <= len(payload):
        length = int.from_bytes(payload[offset : offset + 4], "big")
        offset += 4
        if offset + length > len(payload):
            break
        frames.append(payload[offset : offset + length])
        offset += length
    return frames


def unpack_read_frames(payload: bytes) -> list[bytes]:
    """Inverse of pack_read_frames, keeping the last few frames of a burst."""
    return unpack_tagged_frames(payload, READ_TAG)[-OCR_CONSENSUS_FRAMES:]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )
    app.state.settings = settings

    # Perception first: the no-credentials provider answers from its scene model.
    app.state.perception = PerceptionPipeline(settings)
    app.state.perception.warmup()

    app.state.provider = build_provider(
        settings,
        scene_getter=lambda: app.state.perception.scene,
        detections_getter=lambda: app.state.perception.last_detections,
    )
    app.state.effective_provider = type(app.state.provider).__name__

    app.state.ocr = build_reader(settings.ocr_engine)
    app.state.ocr.warmup()

    log.info("VisionOS ready on %s:%s", settings.host, settings.port)
    yield
    await app.state.provider.aclose()


app = FastAPI(title="VisionOS", lifespan=lifespan)

# The client is served from a different origin during development (Vite on
# :5173, backend on :8000). Local development only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "status": "ok",
            "provider_configured": settings.vision_provider,
            # What is actually serving, which differs when credentials are
            # missing and the configured provider fell back.
            "provider_active": app.state.effective_provider,
            "ocr": app.state.ocr.name,
            "device": app.state.perception.device,
            "lan_address": lan_address(),
            "model": settings.visionos_model,
            "prompt_version": PROMPT_VERSION,
            "demo_mode": settings.demo_mode,
        }
    )


@app.get("/metrics")
async def metrics() -> JSONResponse:
    return JSONResponse(metrics_snapshot())


@app.get("/scene")
async def scene() -> JSONResponse:
    """Live scene model, for debugging and the judge dashboard."""
    return JSONResponse(app.state.perception.snapshot())


class Session:
    """Per-connection state. One phone, one session."""

    def __init__(
        self,
        socket: WebSocket,
        provider: VisionProvider,
        perception: PerceptionPipeline,
        ocr: TextReader,
    ) -> None:
        self.socket = socket
        self.provider = provider
        self.perception = perception
        self.ocr = ocr
        self.latest_frame: bytes | None = None

        settings = get_settings()
        self.hazards_enabled = settings.hazards_enabled
        self.hazards = HazardEngine(
            distance_m=settings.hazard_distance_m,
            cone_deg=settings.hazard_cone_deg,
            cooldown_s=settings.hazard_cooldown_s,
            min_hits=settings.hazard_min_hits,
            min_confidence=settings.hazard_min_confidence,
        )
        self.beacon_label: str | None = None
        # Background reading: (monotonic time, lines) of the last few peeks.
        self.peeks: deque[tuple[float, list]] = deque(maxlen=PEEK_KEEP_MAX)
        self.peek_task: asyncio.Task | None = None
        self.reading = False

    # --- Live frames ------------------------------------------------------

    async def handle_frame(self, frame: bytes) -> None:
        self.latest_frame = frame
        await self.perception.process(frame)
        await self.socket.send_json({
            "type": "detections",
            "items": detection_items(self.perception.last_detections, self.perception.last_frame_size),
        })

        # Deterministic and ahead of everything else: nothing on this path
        # can be delayed by an API call.
        if self.hazards_enabled:
            for alert in self.hazards.evaluate(
                self.perception.scene, self.perception.dropoffs
            ):
                await self.socket.send_json(alert.to_dict())

        await self._update_beacon()

    # --- Reading text -----------------------------------------------------

    async def handle_read(self, frames: list[bytes]) -> None:
        """Answer from the background peeks when they are fresh and solid;
        the burst is the slow path for when they are not."""
        cached = self.fresh_reading()
        if cached and not read_is_weak(cached):
            holder = text_holder(self.perception.scene.all_objects())
            await self._say(with_holder(format_for_speech(cached), holder))
            log.info("read: answered from %d peek(s) via %s", len(self.peeks), self.ocr.name)
            return
        self.reading = True
        try:
            await self._read_burst(frames)
        finally:
            self.reading = False

    async def _read_burst(self, frames: list[bytes]) -> None:
        """Local OCR over the burst first; the vision provider only if that
        finds nothing."""
        frames = [frame for frame in frames if frame]
        if not frames:
            await self._say(NO_CAMERA)
            return

        trace = LatencyTrace(label="read")
        # Fast tier first: one quick pass over the sharpest frame, joined
        # with any fresh peeks. Spoken as it stands when the reader is at
        # least 80% sure of every line and it is not a scrap; the thorough
        # burst is the fallback, not the default. A medical label whose dose
        # the guard would withhold always gets the burst, since only
        # agreement across frames can release a dose.
        # Sharpness once per burst and off the loop: decoding every frame
        # on the asyncio thread, twice on the no-text path, was the read's
        # own worst stall.
        scores = await asyncio.to_thread(lambda: [_sharpness(frame) for frame in frames])
        sharpest = frames[scores.index(max(scores))]
        with trace.stage("quick"):
            first = await self.ocr.read_quick(sharpest)
        lines = self.ocr.combine_readings([*self.fresh_peek_readings(), first])
        settled = (
            all_lines_sure(self.ocr, lines)
            and not read_is_weak(lines)
            and UNREADABLE_DOSE not in format_for_speech(lines)
        )
        if not settled:
            with trace.stage("ocr"):
                lines = await self.ocr.read_consensus(frames)

        # Escalate on a poor read, not only an empty one. Connected script and
        # decorative faces are where OCR fails hardest, and it fails in two
        # ways: returning nothing, or a short implausible scrap. Both mean a
        # vision model should look instead, when there is one.
        if self.provider.reads_text and read_is_weak(lines):
            await self._stream_answer(frames[-1], READ_PROMPT, None, "read", trace)
            return

        if lines:
            holder = text_holder(self.perception.scene.all_objects())
            await self._say(with_holder(format_for_speech(lines), holder))
            log.info(
                "read: %d line(s) via %s from %d frame(s)", len(lines), self.ocr.name, len(frames)
            )
        else:
            await self._say(no_text_response(frames, max(scores)))
        await self._finish(trace)

    # --- Scanning ---------------------------------------------------------

    async def handle_scan(self, frames: list[bytes]) -> None:
        """Describe the scene from a two-frame burst detected at scan size."""
        frames = [frame for frame in frames if frame][:SCAN_FRAMES]
        if not frames:
            await self.handle_intent("scan", None)
            return
        if self.provider.reads_text:
            # A vision model describes the picture itself; the burst just
            # gives it the sharper frame.
            self.latest_frame = frames[-1]
            await self.handle_intent("scan", None)
            return

        trace = LatencyTrace(label="scan")
        loop = asyncio.get_running_loop()
        per_frame = []
        size = None
        with trace.stage("detect"):
            for frame in frames:
                image = await loop.run_in_executor(None, _decode_jpeg, frame)
                if image is None:
                    continue
                size = (image.shape[1], image.shape[0])
                per_frame.append(await self.perception.detector.detect(image, SCAN_IMGSZ))
        if size is None:
            await self._say(NO_CAMERA)
            return

        seen, once = match_scan_frames(per_frame, size)
        seen = add_tracked(seen, self.perception.scene.all_objects())
        # The words on things: one quick read of the first frame, so a bin
        # marked RECYCLE is a recycling bin whatever its outline suggested.
        cues = []
        if self.ocr.available:
            with trace.stage("cues"):
                cues = await self.ocr.read_quick(frames[0])
        seen = refine_labels(seen, cues)
        once = refine_labels(once, cues)
        await self._say(describe_scan(self.perception.scene, seen, once, cues))
        await self.socket.send_json({
            "type": "inventory",
            "text": inventory_sentence(seen, once),
            "items": [
                {"label": s.label, "confidence": round(s.confidence, 2), "frames": s.frames,
                 "azimuth_deg": round(s.azimuth_deg, 1),
                 "distance_m": None if s.distance_m is None else round(s.distance_m, 1),
                 "box": [round(v, 4) for v in s.box] if s.box else None}
                for s in [*seen, *once]
            ],
        })
        await self._finish(trace)

    # --- Background reading -----------------------------------------------

    async def handle_peek(self, frames: list[bytes]) -> None:
        """OCR a frame in the background, never queued and never blocking.

        One peek at a time, none while a read burst is in flight, and a soft
        frame is skipped: the point is to be cheap, and a queued peek would
        describe a label the user has already moved.
        """
        frame = frames[-1] if frames else b""
        if not frame or self.reading:
            return
        if self.peek_task is not None and not self.peek_task.done():
            return
        if await asyncio.to_thread(_sharpness, frame) < PEEK_MIN_SHARPNESS:
            return
        self.peek_task = asyncio.create_task(self._peek(frame))

    async def _peek(self, frame: bytes) -> None:
        try:
            lines = await self.ocr.read_quick(frame)
        except Exception:
            log.exception("peek failed")
            return
        self.forget_stale_peeks()
        if lines:
            self.peeks.append((time.monotonic(), lines))

    def forget_stale_peeks(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        while self.peeks and now - self.peeks[0][0] > PEEK_KEEP_S:
            self.peeks.popleft()

    def fresh_peek_readings(self, now: float | None = None) -> list[list]:
        now = time.monotonic() if now is None else now
        self.forget_stale_peeks(now)
        return [lines for seen_at, lines in self.peeks if now - seen_at <= PEEK_FRESH_S]

    def fresh_reading(self, now: float | None = None) -> list:
        """What the last few seconds of peeks agree is in view; [] if nothing."""
        return self.ocr.combine_readings(self.fresh_peek_readings(now))

    # --- Questions and scans ----------------------------------------------

    async def handle_intent(self, kind: str, text: str | None) -> None:
        if kind == "read":
            await self.handle_read([self.latest_frame] if self.latest_frame else [])
            return

        if self.latest_frame is None:
            await self._say(NO_CAMERA)
            return

        prompt = SCAN_PROMPT if kind == "scan" else (text or SCAN_PROMPT)
        # Ground the answer in tracked geometry rather than pixels alone.
        context = scene_context(self.perception.snapshot())
        await self._stream_answer(
            self.latest_frame, prompt, context, kind, LatencyTrace(label=kind)
        )

    async def _stream_answer(
        self,
        frame: bytes,
        prompt: str,
        context: str | None,
        intent: str,
        trace: LatencyTrace,
    ) -> None:
        """Speak each sentence the moment it completes, not when the answer ends."""
        chunker = SentenceChunker()
        spoke = False

        async for delta in self.provider.describe(frame, prompt, context, intent=intent):
            for sentence in chunker.feed(delta):
                if not spoke:
                    # The number that matters: frame -> first spoken word.
                    trace.mark("first_sentence")
                    spoke = True
                await self._say(sentence)

        if remainder := chunker.flush():
            if not spoke:
                trace.mark("first_sentence")
            await self._say(remainder)

        await self._finish(trace)

    # --- Beacons ----------------------------------------------------------

    async def handle_locate(self, label: str | None) -> None:
        """Start an audio beacon toward a named object."""
        if not label:
            return

        matches = find_object(self.perception.scene, label)
        if not matches:
            self.beacon_label = None
            await self._say(f"I can't see a {label} to guide you to.")
            return

        target = matches[0]
        self.beacon_label = target.label
        distance = target.distance_m
        where = (
            f"about {distance:.1f} meters away"
            if distance is not None
            else "somewhere ahead"
        )
        await self._say(
            f"Guiding you to the {target.label}, at your {target.clock}, {where}. "
            "Turn until the sound is centered."
        )
        await self._update_beacon()

    async def _update_beacon(self) -> None:
        """Keep the audio beacon pointed at its target as the user turns."""
        if self.beacon_label is None:
            return

        matches = find_object(self.perception.scene, self.beacon_label)
        if not matches:
            return

        target = matches[0]
        await self.socket.send_json(
            {
                "type": "beacon",
                "label": target.label,
                "azimuth_deg": round(target.azimuth_deg, 1),
                "distance_m": target.distance_m or 2.0,
                "visible": target.visible,
            }
        )

    async def stop_beacon(self) -> None:
        self.beacon_label = None
        await self.socket.send_json({"type": "beacon_stop"})

    # --- Output -----------------------------------------------------------

    async def _say(self, text: str) -> None:
        await self.socket.send_json({"type": "speech", "text": text})

    async def _finish(self, trace: LatencyTrace) -> None:
        trace.mark("complete")
        await self.socket.send_json({"type": "trace", **trace.to_dict()})
        log.info("%s complete in %.0f ms", trace.label, trace.total_ms)


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    await socket.accept()
    session = Session(socket, app.state.provider, app.state.perception, app.state.ocr)
    settings = get_settings()

    await socket.send_json(
        {
            "type": "ready",
            "provider": settings.vision_provider,
            "provider_active": app.state.effective_provider,
            "ocr": app.state.ocr.name,
            "device": app.state.perception.device,
            "demo_mode": settings.demo_mode,
        }
    )

    try:
        while True:
            message = await socket.receive()
            # A raw receive() hands back the disconnect as a message rather
            # than raising; asking again after it is an error, not a wait.
            if message.get("type") == "websocket.disconnect":
                break

            if (frame := message.get("bytes")) is not None:
                if frame.startswith(READ_TAG):
                    await session.handle_read(unpack_read_frames(frame))
                elif frame.startswith(PEEK_TAG):
                    await session.handle_peek(unpack_tagged_frames(frame, PEEK_TAG))
                elif frame.startswith(SCAN_TAG):
                    await session.handle_scan(unpack_tagged_frames(frame, SCAN_TAG))
                else:
                    await session.handle_frame(frame)
                continue

            if (payload := message.get("text")) is None:
                continue

            event = json.loads(payload)
            kind = event.get("type")

            if kind in {"scan", "read", "ask"}:
                await session.handle_intent(kind, event.get("text"))
            elif kind == "locate":
                await session.handle_locate(event.get("text"))
            elif kind == "stop_beacon":
                await session.stop_beacon()

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("session error")
    log.info("client disconnected")
