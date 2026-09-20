"""Pre-flight check. Run this before demo day, not during it.

The credential section encodes a non-obvious fact: an unset ANTHROPIC_API_KEY
does not mean you have no credentials. The SDK resolves API key -> auth token
-> `ant auth login` OAuth profile on disk, first match wins.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from backend.config import get_settings

OK, WARN, FAIL = "  ok  ", " warn ", " FAIL "


def _line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label}" + (f"  --  {detail}" if detail else ""))


def check_python() -> bool:
    major, minor = sys.version_info[:2]
    # 3.10 is the floor the code actually needs (slots dataclasses, X | Y in
    # annotations under the __future__ import); the full suite runs on it.
    if (major, minor) < (3, 10):
        _line(FAIL, f"Python {major}.{minor}", "need >= 3.10")
        return False
    if (major, minor) >= (3, 13):
        _line(WARN, f"Python {major}.{minor}", "ML wheels are spotty; 3.12 preferred")
        return True
    _line(OK, f"Python {major}.{minor}")
    return True


def check_credentials() -> bool:
    """Resolution order mirrors the Anthropic SDK's own."""
    settings = get_settings()
    if settings.vision_provider == "replay":
        _line(OK, "Credentials", "replay provider -- none needed")
        return True
    if settings.vision_provider == "nvidia":
        if settings.nvidia_api_key:
            _line(OK, "Credentials", f"NVIDIA key set; questions go to {settings.nvidia_model}")
            return True
        _line(FAIL, "Credentials", "VISION_PROVIDER=nvidia but NVIDIA_API_KEY is empty -- on-device answers only")
        return False
    if settings.vision_provider == "local":
        _line(OK, "Credentials", "local provider -- none needed; no open questions")
        return True

    if os.environ.get("ANTHROPIC_API_KEY"):
        _line(OK, "Credentials", "ANTHROPIC_API_KEY set")
        return True
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        _line(OK, "Credentials", "ANTHROPIC_AUTH_TOKEN set")
        return True

    if shutil.which("ant"):
        result = subprocess.run(
            ["ant", "auth", "status"], capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            _line(OK, "Credentials", "active `ant auth login` profile")
            return True
        _line(FAIL, "Credentials", "ant installed but no active profile -- run: ant auth login")
        return False

    if (Path.home() / ".config" / "anthropic").exists():
        _line(WARN, "Credentials", "profile dir exists but `ant` CLI is missing")
        return False

    _line(
        FAIL,
        "Credentials",
        "none found. Either run `ant auth login` (no API key needed), "
        "set ANTHROPIC_API_KEY, or run with VISION_PROVIDER=replay",
    )
    return False


def check_weights() -> bool:
    """Uncached weights mean the safety layer dies with the WiFi."""
    settings = get_settings()
    ok = True

    weights = (
        settings.open_vocab_weights
        if settings.detector_mode == "open"
        else settings.detector_weights
    )
    if Path(weights).exists():
        _line(OK, "Detector weights", weights)
    else:
        try:
            from ultralytics.utils import SETTINGS as ULTRA  # noqa: N811

            cached = Path(ULTRA.get("weights_dir", "")) / weights
            if cached.exists():
                _line(OK, "Detector weights", str(cached))
            else:
                _line(WARN, "Detector weights", f"{weights} not cached -- run: make precache")
                ok = False
        except Exception:
            _line(WARN, "Detector weights", f"{weights} not cached -- run: make precache")
            ok = False

    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    if hf_cache.exists() and any(hf_cache.glob("*epth*")):
        _line(OK, "Depth weights", "cached")
    else:
        _line(WARN, "Depth weights", "not cached -- run: make precache")
        ok = False

    return ok


def check_ocr() -> bool:
    """Without a local engine every read costs a network round trip."""
    from backend.ai.ocr import build_reader

    reader = build_reader(get_settings().ocr_engine)
    if reader.available:
        _line(OK, "OCR engine", reader.name)
        return True
    _line(
        WARN,
        "OCR engine",
        "none loaded -- pip install rapidocr-onnxruntime "
        "(or pyobjc-framework-Vision on macOS)",
    )
    return False


def check_port() -> bool:
    port = get_settings().port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            _line(WARN, f"Port {port}", "already in use")
            return False
    _line(OK, f"Port {port}", "free")
    return True


def check_scanner() -> bool:
    """The allergy scanner and the voice: never demo-blocking, always said."""
    from backend.alerts.email_agent import EmailAgent
    from backend.alerts.profile import ProfileStore

    settings = get_settings()
    profile = ProfileStore(settings.profile_file).current
    if profile.allergens:
        to = profile.doctor_email or settings.alert_email_to or "nobody yet (add the doctor in the app, key L)"
        _line(OK, "Allergy scanner", f"watching for {', '.join(profile.allergens)}; alerts to {to}")
    else:
        _line(WARN, "Allergy scanner", f"idle: no allergens in {settings.profile_file} (key L in the app)")

    mail = EmailAgent(user=settings.alert_smtp_user, password=settings.alert_smtp_password, to=settings.alert_email_to)
    if mail.configured:
        _line(OK, "Alert mail", "set up; alerts are emailed")
    else:
        _line(WARN, "Alert mail", f"not set up; alerts go to {settings.alert_outbox_dir} (ALERT_SMTP_USER, ALERT_SMTP_PASSWORD)")

    try:
        import cv2

        barcode = hasattr(cv2, "barcode")
    except Exception:
        barcode = False
    _line(OK if barcode else WARN, "Barcodes", "OpenCV decodes them; Open Food Facts lookup" if barcode else "no barcode module in this OpenCV; labels only")

    if getattr(settings, "elevenlabs_api_key", ""):
        _line(OK, "Voice", "ElevenLabs for answers and reads; browser voice for warnings")
    else:
        _line(OK, "Voice", "the browser's own (ELEVENLABS_API_KEY for a better one)")
    return True


def main() -> int:
    print("VisionOS pre-flight\n")
    results = [
        check_python(),
        check_credentials(),
        check_weights(),
        check_ocr(),
        check_scanner(),
        check_port(),
    ]
    print()
    if all(results):
        print("All clear.")
        return 0
    print("Issues above. Demo-blocking ones are marked FAIL.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
