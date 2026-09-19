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
    # "low" keeps tool calls consolidated and preamble short -- what a voice
    # assistant wants. Raise to "high" only if answer quality measurably drops.
    visionos_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    visionos_max_tokens: int = 1024

    # --- Vision provider --------------------------------------------------
    # "claude"  full open-vocabulary vision; needs credentials.
    # "local"   describes the real scene model from YOLO alone. Truthful with
    #           no network, but limited to 80 COCO classes and cannot read text.
    # "replay"  canned scenes. Deterministic, and the only mode that can be
    #           confidently WRONG -- it will describe a living room while the
    #           camera points at a parking lot. Rehearsal and tests only.
    #
    # "claude" degrades to "local", not "replay", when no credentials resolve:
    # a truthful partial answer beats a confident fabrication.
    vision_provider: Literal["claude", "local", "replay"] = "claude"
    replay_fixture: str = "assets/replay/livingroom.json"

    # --- Text reading -----------------------------------------------------
    # "auto" takes the first engine that loads: Apple Vision on macOS (needs
    # pyobjc), then RapidOCR anywhere. "none" sends every read to the vision
    # provider, which costs a network round trip.
    ocr_engine: Literal["auto", "apple-vision", "rapidocr", "none"] = "auto"

    # --- Local perception -------------------------------------------------
    # "open" detects the curated vocabulary in vocabulary.py, including doors,
    # stairs and handrails that COCO lacks entirely. Measured cheaper than the
    # medium closed-set model (25 ms vs 43 ms), so it is the default.
    # "coco" falls back to a fixed checkpoint if open vocabulary proves noisy.
    detector_mode: Literal["open", "coco"] = "open"
    open_vocab_weights: str = "yolov8s-world.pt"
    detector_weights: str = "yolo11m.pt"
    # Open-vocabulary scores run lower than closed-set ones; dangerous classes
    # get a stricter floor of their own in vocabulary.py. Raised from 0.12
    # after real-world use produced a stream of flickering false detections,
    # and from 0.20 after 300 COCO photographs (eval/run_detect_eval.py): at
    # 0.20 only 54% of the detector's claims were real, at 0.35 it is 69%,
    # for recall 57% -> 48%; the two halves of the photo set agree, and a
    # table of per-class floors fitted on one half did worse on the other.
    # 0.40 is the next notch (73% / 46%) if invented things persist.
    detector_confidence: float = 0.35
    depth_model: str = "depth-anything/Depth-Anything-V2-Small-hf"
    # Horizontal FOV of a typical phone rear camera. Drives pixel->azimuth.
    camera_hfov_deg: float = 66.0
    perception_device: Literal["auto", "cpu", "mps", "cuda"] = "auto"

    # --- Hazards (safety-critical; no LLM in this path) -------------------
    # Off by default while the open-vocabulary vocabulary is being tuned:
    # false warnings interrupt everything else and make the rest of the system
    # hard to evaluate. The engine is intact and tested -- flip this to re-arm.
    hazards_enabled: bool = False
    hazard_distance_m: float = 1.5
    hazard_cone_deg: float = 30.0
    hazard_cooldown_s: float = 3.0
    # An interruption costs more than a description, so alerts need more
    # evidence: several frames of persistence and a higher confidence floor.
    # Without these, open-vocabulary flicker produced constant false warnings.
    hazard_min_hits: int = 3
    hazard_min_confidence: float = 0.45  # a step above detector_confidence

    # --- Scene model ------------------------------------------------------
    # How long a departed object stays remembered ("it was there a moment ago").
    object_memory_s: float = 20.0
    track_iou_threshold: float = 0.3

    # --- Server -----------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    # Seeds fixed behavior and enables replay. Set for every rehearsal.
    demo_mode: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
