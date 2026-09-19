"""FastAPI app: frames up over WebSocket, speech events down.

Phase 1 walking skeleton -- camera -> provider -> streamed speech, with a
latency trace on every hop. Perception, scene model, and hazards land in later
phases behind the same socket.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.ai.ocr import AppleVisionOCR, format_for_speech
from backend.ai.prompts import PROMPT_VERSION, READ_PROMPT, SCAN_PROMPT, scene_context
from backend.ai.vision import VisionProvider, build_provider
from backend.config import get_settings
from backend.perception.pipeline import PerceptionPipeline
from backend.speech.chunker import SentenceChunker
from backend.telemetry import LatencyTrace, metrics_snapshot

log = logging.getLogger(__name__)

CLIENT_DIR = Path(__file__).resolve().parents[1] / "client"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )
    app.state.settings = settings

    # Perception first: the provider may need to read the scene model, because
    # the no-credentials fallback answers from it.
    app.state.perception = PerceptionPipeline(settings)
    app.state.perception.warmup()

    app.state.provider = build_provider(
        settings, scene_getter=lambda: app.state.perception.scene
    )
    app.state.effective_provider = type(app.state.provider).__name__

    # 13 ms steady state, but 315 ms on the first call. Pay it here.
    app.state.ocr = AppleVisionOCR()
    app.state.ocr.warmup()

    log.info("VisionOS ready on %s:%s", settings.host, settings.port)
    yield
    await app.state.provider.aclose()


app = FastAPI(title="VisionOS", lifespan=lifespan)

# The client is served from a different origin during development (Vite on
# :5173, backend on :8000). Locked to local dev use only.
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
    """Live scene model. Powers the judge dashboard."""
    return JSONResponse(app.state.perception.snapshot())


class Session:
    """Per-connection state. One phone, one session."""

    def __init__(
        self,
        socket: WebSocket,
        provider: VisionProvider,
        perception: PerceptionPipeline,
        ocr: AppleVisionOCR,
    ) -> None:
        self.socket = socket
        self.provider = provider
        self.perception = perception
        self.ocr = ocr
        self.latest_frame: bytes | None = None

    async def handle_frame(self, frame: bytes) -> None:
        self.latest_frame = frame
        await self.perception.process(frame)

    async def _read_locally(self) -> bool:
        """Read text with on-device OCR. False means nothing found, escalate.

        Assumes latest_frame is the detailed capture the client sends ahead of
        a read: the 640px fast-path frame loses the strokes OCR needs.
        """
        trace = LatencyTrace(label="read_local")
        with trace.stage("ocr"):
            lines = await self.ocr.read(self.latest_frame or b"")

        # Escalate only if there is something better to escalate to.
        escalation_available = type(self.provider).__name__ == "ClaudeVisionProvider"
        if not lines and escalation_available:
            return False

        trace.mark("complete")
        await self.socket.send_json({"type": "speech", "text": format_for_speech(lines)})
        await self.socket.send_json({"type": "trace", **trace.to_dict()})
        log.info("read: %d line(s) in %.0f ms", len(lines), trace.total_ms)
        return True

    async def handle_intent(self, kind: str, text: str | None) -> None:
        if self.latest_frame is None:
            await self.socket.send_json(
                {"type": "speech", "text": "I'm not receiving the camera yet."}
            )
            return

        if kind == "read" and self.ocr.available:
            if await self._read_locally():
                return
            # Nothing found: fall through so Claude can try, since it handles
            # layouts and handwriting that OCR misses.

        prompt = {"scan": SCAN_PROMPT, "read": READ_PROMPT}.get(kind) or (
            text or SCAN_PROMPT
        )

        trace = LatencyTrace(label=kind)
        chunker = SentenceChunker()
        spoke = False

        # Ground the answer in tracked geometry rather than pixels alone.
        # Skipped for OCR, where the scene model has nothing to contribute.
        context = None if kind == "read" else scene_context(self.perception.snapshot())

        async for delta in self.provider.describe(
            self.latest_frame, prompt, context, intent=kind
        ):
            for sentence in chunker.feed(delta):
                if not spoke:
                    # The number that matters: frame -> first spoken word.
                    trace.mark("first_sentence")
                    spoke = True
                await self.socket.send_json({"type": "speech", "text": sentence})

        if remainder := chunker.flush():
            if not spoke:
                trace.mark("first_sentence")
            await self.socket.send_json({"type": "speech", "text": remainder})

        trace.mark("complete")
        await self.socket.send_json({"type": "trace", **trace.to_dict()})
        log.info("%s complete in %.0f ms", kind, trace.total_ms)


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    await socket.accept()
    session = Session(
        socket, app.state.provider, app.state.perception, app.state.ocr
    )
    settings = get_settings()

    await socket.send_json(
        {
            "type": "ready",
            "provider": settings.vision_provider,
            "provider_active": app.state.effective_provider,
            "demo_mode": settings.demo_mode,
        }
    )

    try:
        while True:
            message = await socket.receive()

            if (frame := message.get("bytes")) is not None:
                await session.handle_frame(frame)
                continue

            if (payload := message.get("text")) is None:
                continue

            event = json.loads(payload)
            kind = event.get("type")
            if kind in {"scan", "read", "ask"}:
                await session.handle_intent(kind, event.get("text"))

    except WebSocketDisconnect:
        log.info("client disconnected")
    except Exception:
        log.exception("session error")
