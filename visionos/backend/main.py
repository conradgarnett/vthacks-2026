"""FastAPI app: frames up over WebSocket, speech events down.

Wire protocol, one phone per socket:

  binary  JPEG                         live frame for perception (small, frequent)
  binary  b"READ" + (u32 len + JPEG)*  a burst of detailed frames to read text from
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

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.ai.ocr import NO_TEXT_FOUND, TextReader, build_reader, format_for_speech
from backend.ai.prompts import PROMPT_VERSION, READ_PROMPT, SCAN_PROMPT, scene_context
from backend.ai.vision import VisionProvider, build_provider
from backend.config import get_settings
from backend.hazards.engine import HazardEngine
from backend.perception.pipeline import PerceptionPipeline
from backend.scene.queries import find_object
from backend.speech.chunker import SentenceChunker
from backend.telemetry import LatencyTrace, metrics_snapshot

log = logging.getLogger(__name__)

READ_TAG = b"READ"
# Three is the useful minimum for majority agreement; more costs latency the
# read budget cannot spare.
OCR_CONSENSUS_FRAMES = 3
NO_CAMERA = "I'm not receiving the camera yet."


def pack_read_frames(frames: list[bytes]) -> bytes:
    """READ tag, then each frame as a big-endian u32 length plus JPEG bytes.

    Mirrors sendReadFrames() in client/src/ws.ts.
    """
    return READ_TAG + b"".join(len(frame).to_bytes(4, "big") + frame for frame in frames)


def unpack_read_frames(payload: bytes) -> list[bytes]:
    """Inverse of pack_read_frames. A truncated tail is dropped, not guessed at."""
    frames: list[bytes] = []
    offset = len(READ_TAG)
    while offset + 4 <= len(payload):
        length = int.from_bytes(payload[offset : offset + 4], "big")
        offset += 4
        frame = payload[offset : offset + length]
        if len(frame) != length:
            break
        frames.append(frame)
        offset += length
    return frames[-OCR_CONSENSUS_FRAMES:]


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
        settings, scene_getter=lambda: app.state.perception.scene
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

    # --- Live frames ------------------------------------------------------

    async def handle_frame(self, frame: bytes) -> None:
        self.latest_frame = frame
        await self.perception.process(frame)

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
        """Local OCR over the burst first; the vision provider only if that
        finds nothing."""
        frames = [frame for frame in frames if frame]
        if not frames:
            await self._say(NO_CAMERA)
            return

        trace = LatencyTrace(label="read")
        with trace.stage("ocr"):
            lines = await self.ocr.read_consensus(frames)

        if lines:
            await self._say(format_for_speech(lines))
            await self._finish(trace)
            log.info(
                "read: %d line(s) via %s from %d frame(s)", len(lines), self.ocr.name, len(frames)
            )
            return

        if self.provider.reads_text:
            # Claude handles the layouts and handwriting OCR misses.
            await self._stream_answer(frames[-1], READ_PROMPT, None, "read", trace)
            return

        await self._say(NO_TEXT_FOUND)
        await self._finish(trace)

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
            "demo_mode": settings.demo_mode,
        }
    )

    try:
        while True:
            message = await socket.receive()

            if (frame := message.get("bytes")) is not None:
                if frame.startswith(READ_TAG):
                    await session.handle_read(unpack_read_frames(frame))
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
        log.info("client disconnected")
    except Exception:
        log.exception("session error")
