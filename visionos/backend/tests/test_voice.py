"""The voice must never be the reason nothing is said.

Every failure mode here has the same correct answer -- return None so the
client speaks it with the browser's own voice -- and none of them may raise.
"""

from __future__ import annotations

import httpx
import pytest

from backend.config import Settings
from backend.speech.voice import MAX_CHARS, Voice


def _settings(tmp_path, **over) -> Settings:
    base = dict(
        elevenlabs_api_key="test-key",
        elevenlabs_voice_id="voice-1",
        elevenlabs_model="model-1",
        elevenlabs_cache_dir=str(tmp_path / "cache"),
    )
    base.update(over)
    return Settings(**base)


@pytest.mark.asyncio
async def test_no_key_is_silent_not_an_error(tmp_path):
    voice = Voice(_settings(tmp_path, elevenlabs_api_key=""))
    assert voice.enabled is False
    assert await voice.synthesize("Stairs ahead.") is None


@pytest.mark.asyncio
async def test_overlong_text_is_refused(tmp_path):
    voice = Voice(_settings(tmp_path))
    assert await voice.synthesize("x" * (MAX_CHARS + 1)) is None


@pytest.mark.asyncio
async def test_empty_text_is_refused(tmp_path):
    voice = Voice(_settings(tmp_path))
    assert await voice.synthesize("   ") is None


@pytest.mark.asyncio
async def test_synthesizes_once_then_serves_from_disk(tmp_path, monkeypatch):
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(200, content=b"ID3-audio")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    voice = Voice(_settings(tmp_path))

    first = await voice.synthesize("Watch connected.")
    second = await voice.synthesize("Watch connected.")

    assert first == second == b"ID3-audio"
    # The point of the cache: the second utterance costs no request.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cache_key_covers_voice_and_model(tmp_path, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(200, content=b"audio")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    text = "Watch connected."

    a = Voice(_settings(tmp_path))
    await a.synthesize(text)
    # A different voice must not be served the first voice's audio.
    b = Voice(_settings(tmp_path, elevenlabs_voice_id="voice-2"))
    assert b.cached(text) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 422, 500])
async def test_http_failures_fall_back(tmp_path, monkeypatch, status):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(status, content=b"nope")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    voice = Voice(_settings(tmp_path))
    assert await voice.synthesize("Stairs ahead.") is None
    # A failure must not poison the cache.
    assert voice.cached("Stairs ahead.") is None


@pytest.mark.asyncio
async def test_network_error_falls_back(tmp_path, monkeypatch):
    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    voice = Voice(_settings(tmp_path))
    assert await voice.synthesize("Stairs ahead.") is None


@pytest.mark.asyncio
async def test_api_key_is_sent_as_header_not_query(tmp_path, monkeypatch):
    seen = {}

    async def fake_post(self, url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers", {})
        return httpx.Response(200, content=b"audio")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    await Voice(_settings(tmp_path)).synthesize("Hello.")

    assert seen["headers"]["xi-api-key"] == "test-key"
    # A key in a URL lands in logs and proxies.
    assert "test-key" not in seen["url"]
