"""Runtime configuration. Everything is env-overridable; nothing is hardcoded.

Credential note: we never read ANTHROPIC_API_KEY ourselves. A bare
`anthropic.Anthropic()` resolves, in order, ANTHROPIC_API_KEY ->
ANTHROPIC_AUTH_TOKEN -> an `ant auth login` OAuth profile on disk. Reading the
key here would break the keyless profile path.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Claude -----------------------------------------------------------
    # Live conversational loop. Sonnet-class: latency is the product here.
    visionos_model: str = "claude-sonnet-5"
    # Escalation path for hard questions where accuracy beats speed.
    visionos_model_heavy: str = "claude-opus-5"
    # "low" keeps tool calls consolidated and preamble short -- what a voice
    # assistant wants. Raise to "high" only if answer quality measurably drops.
    visionos_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    visionos_max_tokens: int = 1024

    # --- Vision provider --------------------------------------------------
    # "claude" hits the API. "replay" serves canned scenes: no credentials, no
    # network, deterministic. The demo fallback when hackathon WiFi dies.
    vision_provider: Literal["claude", "replay"] = "claude"
    replay_fixture: str = "assets/replay/livingroom.json"

    # --- Local perception -------------------------------------------------
    detector_weights: str = "yolo11n.pt"
    detector_confidence: float = 0.35
    depth_model: str = "depth-anything/Depth-Anything-V2-Small-hf"
    # Horizontal FOV of a typical phone rear camera. Drives pixel->azimuth.
    camera_hfov_deg: float = 66.0
    perception_device: Literal["auto", "cpu", "mps", "cuda"] = "auto"

    # --- Hazards (safety-critical; no LLM in this path) -------------------
    hazard_distance_m: float = 1.5
    hazard_cone_deg: float = 30.0
    hazard_cooldown_s: float = 3.0
    # Below this, a depth reading is too noisy to assert a hazard from.
    hazard_min_confidence: float = 0.45

    # --- Scene model ------------------------------------------------------
    # How long a departed object stays remembered ("it was there a moment ago").
    object_memory_s: float = 20.0
    track_iou_threshold: float = 0.3

    # --- Speech -----------------------------------------------------------
    # "browser" needs no keys and works offline via speechSynthesis.
    tts_provider: Literal["browser", "elevenlabs", "openai"] = "browser"
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    openai_api_key: str | None = None

    # --- Server -----------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    # Seeds fixed behavior and enables replay. Set for every rehearsal.
    demo_mode: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
