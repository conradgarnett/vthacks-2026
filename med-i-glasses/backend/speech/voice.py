"""ElevenLabs speech, cached to disk.

Why this exists: the browser's own voice is instant and survives the WiFi
dying, which is why hazards still use it (see client/src/audio/tts-player.ts).
But it sounds synthetic, and the things a user listens to closely -- a label
being read back, an answer to a question -- are exactly where that costs
credibility.

So this is the unhurried path, and it is cache-first. Every phrase the app
says verbatim is synthesized once and then served from disk forever after:
better than live on quality AND on latency, and it costs nothing after the
first time. Dynamic text (the actual words on a pill bottle) goes to the API
on first utterance and is cached in case it is read twice.

Failure is never fatal. No key, a timeout, a dead network, a quota that ran
out: synthesize() returns None and the client speaks it with its own voice.
A silent assistant is the one outcome this must not produce.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx

from backend.config import Settings

log = logging.getLogger(__name__)

_API = "https://api.elevenlabs.io/v1/text-to-speech"

# Long text is a runaway quota bill and never a real utterance -- the reader
# chunks before it gets here. Refuse rather than truncate: half a sentence
# spoken confidently is worse than the browser saying the whole thing.
MAX_CHARS = 600


class Voice:
    """Cache-first ElevenLabs client. Never raises to the caller."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache = Path(settings.elevenlabs_cache_dir)
        self._cache.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self._settings.elevenlabs_api_key)

    def _path(self, text: str) -> Path:
        """Cache key covers voice and model: changing either must re-render."""
        key = f"{self._settings.elevenlabs_voice_id}|{self._settings.elevenlabs_model}|{text}"
        return self._cache / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.mp3"

    def cached(self, text: str) -> bytes | None:
        path = self._path(text)
        if path.is_file():
            return path.read_bytes()
        return None

    async def synthesize(self, text: str) -> bytes | None:
        """Audio for `text`, or None if the caller should speak it itself."""
        text = text.strip()
        if not text or not self.enabled:
            return None
        if len(text) > MAX_CHARS:
            log.warning("voice: %d chars exceeds %d, leaving it to the client", len(text), MAX_CHARS)
            return None

        hit = self.cached(text)
        if hit is not None:
            return hit

        url = f"{_API}/{self._settings.elevenlabs_voice_id}"
        try:
            async with httpx.AsyncClient(timeout=self._settings.elevenlabs_timeout_s) as client:
                response = await client.post(
                    url,
                    headers={
                        "xi-api-key": self._settings.elevenlabs_api_key,
                        "Content-Type": "application/json",
                    },
                    json={"text": text, "model_id": self._settings.elevenlabs_model},
                )
        except httpx.HTTPError as exc:
            # Network down, DNS gone, timed out. The client has a voice.
            log.warning("voice: %s -- falling back to the browser", exc)
            return None

        if response.status_code != 200:
            # 401 bad key, 402 quota, 422 bad voice. Same answer to all of
            # them: say nothing about it and let the browser speak.
            log.warning("voice: http %s -- falling back to the browser", response.status_code)
            return None

        audio = response.content
        # Write through a temp name so a killed process cannot leave a
        # truncated mp3 that would be served as a cache hit forever.
        path = self._path(text)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(path)
        return audio
