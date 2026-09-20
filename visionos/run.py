#!/usr/bin/env python3
"""One command to run VisionOS on macOS, Linux or Windows.

    python run.py             checks the machine, asks what it needs to, starts
    python run.py --yes       take the defaults, ask nothing
    python run.py --local     plain HTTP on localhost, for a browser on this PC
    python run.py --phone     HTTPS on the LAN, for a phone on the same Wi-Fi
    python run.py --check     report what would happen and exit

The first run creates the venv, installs the backend and client dependencies,
and downloads the model weights. Every run works out which vision provider and
OCR engine this machine can actually use, says so, and starts the backend and
the client together. `make dev` does the same on a Mac with the tools already
on PATH; this needs only Python and Node installed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLIENT = ROOT / "client"
VENV = ROOT / ".venv"
WINDOWS = sys.platform == "win32"

BACKEND_PORT = 8000
PHONE_PORT = 5173  # HTTPS, self-signed
LOCAL_PORT = 5174  # HTTP, localhost only is a secure context


def say(text: str) -> None:
    print(f"  {text}")


def ask(question: str, default: str, assume_yes: bool) -> str:
    if assume_yes or not sys.stdin.isatty():
        return default
    answer = input(f"\n{question} [{default}] ").strip()
    return answer or default


def fail(text: str) -> NoReturn:  # noqa: F821 - typing only
    print(f"\nERROR: {text}")
    raise SystemExit(1)


# --- tools ------------------------------------------------------------------


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def find_node_dir() -> Path | None:
    """Node's folder, even when the installer did not put it on PATH."""
    for name in ("node", "node.exe"):
        found = shutil.which(name)
        if found:
            return Path(found).parent
    candidates = [
        Path(r"C:\Program Files\nodejs"),
        Path(r"C:\Program Files (x86)\nodejs"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ]
    for folder in candidates:
        if (folder / ("node.exe" if WINDOWS else "node")).exists():
            return folder
    return None


def npm_command(node_dir: Path) -> list[str]:
    return [str(node_dir / ("npm.cmd" if WINDOWS else "npm"))]


def env_with_node(node_dir: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(node_dir) + os.pathsep + env.get("PATH", "")
    env.update(extra)
    return env


# --- setup ------------------------------------------------------------------


def ensure_python() -> None:
    if sys.version_info < (3, 10):
        fail(f"Python 3.10 or newer is needed; this is {platform.python_version()}.")
    say(f"Python {platform.python_version()} on {platform.system()}")


def ensure_venv() -> None:
    requirements = ROOT / "requirements.txt"
    stamp = VENV / ".installed"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()[:16]

    if not venv_python().exists():
        say("Creating the backend virtual environment (.venv)")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)

    if stamp.exists() and stamp.read_text().strip() == digest:
        say("Backend dependencies up to date")
        return

    say("Installing backend dependencies (torch and friends: a few hundred MB, one time)")
    subprocess.run([str(venv_python()), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run(
        [str(venv_python()), "-m", "pip", "install", "--quiet", "-r", str(requirements)],
        check=True,
    )
    stamp.write_text(digest)


def ensure_client(node_dir: Path) -> None:
    if (CLIENT / "node_modules").exists():
        say("Client dependencies present")
        return
    say("Installing client dependencies (npm install)")
    subprocess.run(
        npm_command(node_dir) + ["install", "--no-audit", "--no-fund"],
        cwd=CLIENT, env=env_with_node(node_dir), check=True,
    )


def ensure_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copy(ROOT / ".env.example", env_file)
        say("Created .env from .env.example")
    # This machine's memory (the places, the profile, the outbox, the voice
    # cache) lives here. It is gitignored, so a fresh clone has no folder.
    (ROOT / "data").mkdir(exist_ok=True)


def dotenv_value(key: str) -> str | None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'") or None
    return None


def credentials_present() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if dotenv_value("ANTHROPIC_API_KEY") or dotenv_value("ANTHROPIC_AUTH_TOKEN"):
        return True
    if shutil.which("ant"):
        try:
            return subprocess.run(
                ["ant", "auth", "status"], capture_output=True, timeout=10
            ).returncode == 0
        except Exception:
            return False
    return False


def nvidia_present() -> bool:
    """Ask goes to NVIDIA's hosted model when the provider says so and a key is there."""
    provider = (os.environ.get("VISION_PROVIDER") or dotenv_value("VISION_PROVIDER") or "claude").lower()
    return provider == "nvidia" and bool(os.environ.get("NVIDIA_API_KEY") or dotenv_value("NVIDIA_API_KEY"))


def scanner_report() -> list[str]:
    """What the allergy scanner and the voice would do on this machine, read
    from the backend's own settings: the profile, mail, the barcode decoder,
    the voice key. Nothing here prints a secret."""
    line = backend_probe(
        "import logging; logging.disable(logging.CRITICAL)\n"
        "from backend.config import get_settings\n"
        "from backend.alerts.profile import ProfileStore\n"
        "from backend.alerts.email_agent import EmailAgent\n"
        "s = get_settings(); p = ProfileStore(s.profile_file).current\n"
        "mail = EmailAgent(user=s.alert_smtp_user, password=s.alert_smtp_password, to=s.alert_email_to)\n"
        "try:\n"
        "    import cv2; barcode = hasattr(cv2, 'barcode')\n"
        "except Exception:\n"
        "    barcode = False\n"
        "print('|'.join([','.join(p.allergens), p.doctor_email or s.alert_email_to or '',"
        " 'yes' if mail.configured else 'no', 'yes' if barcode else 'no',"
        " 'yes' if getattr(s, 'elevenlabs_api_key', '') else 'no']))"
    )
    parts = line.split("|") if line.count("|") == 4 else ["", "", "no", "no", "no"]
    allergens, doctor, mail, barcode, voice = parts
    out: list[str] = []
    if allergens:
        out.append(f"Allergies: watching for {allergens.replace(',', ', ')}; alerts to {doctor or 'nobody yet: add the doctor in the app (key L)'}")
    else:
        out.append("Allergies: none on file, so the scanner is idle (press L in the app to add them)")
    if mail == "yes":
        out.append("Mail: set up; allergy alerts are emailed")
    else:
        out.append("Mail: not set up; alerts are written to data/outbox (ALERT_SMTP_USER and")
        out.append("      ALERT_SMTP_PASSWORD in visionos/.env turn it on)")
    out.append(
        "Barcodes: decoded by OpenCV and looked up in Open Food Facts" if barcode == "yes"
        else "Barcodes: this OpenCV has no barcode module; labels only"
    )
    out.append(
        "Voice: ElevenLabs for answers and reads (browser voice for warnings)" if voice == "yes"
        else "Voice: the browser's own (ELEVENLABS_API_KEY in visionos/.env for a better one)"
    )
    return out


def backend_probe(code: str) -> str:
    """Run a snippet inside the venv from the backend's own directory."""
    result = subprocess.run(
        [str(venv_python()), "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    return (result.stdout or "").strip().splitlines()[-1] if result.stdout.strip() else ""


def ocr_engine_name() -> str:
    return backend_probe(
        "import logging; logging.disable(logging.CRITICAL)\n"
        "from backend.config import get_settings\n"
        "from backend.ai.ocr import build_reader\n"
        "print(build_reader(get_settings().ocr_engine).name)"
    ) or "none"


def weights_cached() -> bool:
    return backend_probe(
        "import logging; logging.disable(logging.CRITICAL)\n"
        "from pathlib import Path\n"
        "from backend.config import get_settings\n"
        "s = get_settings()\n"
        "name = s.open_vocab_weights if s.detector_mode == 'open' else s.detector_weights\n"
        "ok = Path(name).exists()\n"
        "if not ok:\n"
        "    try:\n"
        "        from ultralytics.utils import SETTINGS\n"
        "        ok = (Path(SETTINGS.get('weights_dir', '')) / name).exists()\n"
        "    except Exception:\n"
        "        ok = False\n"
        "print('yes' if ok else 'no')"
    ) == "yes"


def precache() -> None:
    say("Downloading model weights (detector, CLIP text encoder, depth model). One time.")
    steps = [
        "from backend.config import get_settings; from backend.perception.vocabulary import CLASS_NAMES; "
        "from ultralytics import YOLO, YOLOWorld; s = get_settings(); "
        "YOLOWorld(s.open_vocab_weights).set_classes(CLASS_NAMES); YOLO(s.detector_weights)",
        "from backend.config import get_settings; from transformers import pipeline; "
        "pipeline('depth-estimation', model=get_settings().depth_model)",
        "from backend.config import get_settings; from backend.ai.ocr import build_reader; "
        "build_reader(get_settings().ocr_engine).warmup()",
    ]
    for code in steps:
        subprocess.run([str(venv_python()), "-c", code], cwd=ROOT, check=True)


# --- run --------------------------------------------------------------------


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def wait_for_port(port: int, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not port_free(port):
            return True
        time.sleep(0.5)
    return False


def start_backend() -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(venv_python()), "-m", "uvicorn", "backend.main:app",
            "--host", "0.0.0.0", "--port", str(BACKEND_PORT),
        ],
        cwd=ROOT,
    )


def start_client(node_dir: Path, plain_http: bool) -> subprocess.Popen:
    args = npm_command(node_dir) + ["run", "dev", "--", "--host"]
    extra = {}
    if plain_http:
        args += ["--port", str(LOCAL_PORT)]
        extra["VISIONOS_HTTP"] = "1"
    return subprocess.Popen(args, cwd=CLIENT, env=env_with_node(node_dir, **extra))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", "-y", action="store_true", help="take the defaults, ask nothing")
    parser.add_argument("--local", action="store_true", help="browser on this computer (HTTP)")
    parser.add_argument("--phone", action="store_true", help="phone on the LAN (HTTPS)")
    parser.add_argument("--check", action="store_true", help="report and exit without starting")
    args = parser.parse_args()

    print("\nVisionOS launcher\n")
    ensure_python()

    node_dir = find_node_dir()
    if node_dir is None:
        fail("Node.js was not found. Install it from https://nodejs.org (LTS) and run this again.")
    say(f"Node at {node_dir}")

    ensure_venv()
    ensure_client(node_dir)
    ensure_env_file()

    # --- what this machine can do -------------------------------------
    engine = ocr_engine_name()
    if engine == "none":
        say("OCR: no engine loaded. Text will only be read through Claude.")
    else:
        say(f"OCR: {engine} (on-device text reading)")

    has_claude = credentials_present()
    if has_claude:
        say("Vision: Claude (credentials found). Full scene descriptions and questions.")
    elif nvidia_present():
        say("Vision: NVIDIA (key found). Questions go online; scans and reads stay on-device.")
    else:
        say("Vision: no Claude or NVIDIA key. On-device mode: reads text, describes")
        say("        recognized objects, but cannot answer open questions.")
        say("        Add NVIDIA_API_KEY=... (with VISION_PROVIDER=nvidia) or")
        say("        ANTHROPIC_API_KEY=... to visionos/.env for full answers.")
        if ask("Continue in on-device mode? (y/n)", "y", args.yes).lower().startswith("n"):
            return 1
    for line in scanner_report():
        say(line)

    if not weights_cached():
        answer = ask("Model weights are not downloaded yet (~450 MB, one time). Download now? (y/n)", "y", args.yes)
        if answer.lower().startswith("n"):
            say("Without weights the detector cannot start; scans will be empty.")
        else:
            precache()
    else:
        say("Model weights cached")

    # --- where it will be used ----------------------------------------
    if args.local and not args.phone:
        target = "2"
    elif args.phone and not args.local:
        target = "1"
    else:
        default = "2" if WINDOWS else "3"
        target = ask(
            "Where will you use it?\n"
            "  1  a phone on this Wi-Fi (HTTPS; accept the certificate warning once)\n"
            "  2  a browser on this computer (HTTP on localhost; camera works)\n"
            "  3  both",
            default, args.yes,
        )
    want_phone = target in ("1", "3")
    want_local = target in ("2", "3")

    ip = lan_ip()
    print("\nPlan:")
    if want_phone:
        say(f"phone:    https://{ip}:{PHONE_PORT}")
    if want_local:
        say(f"this PC:  http://localhost:{LOCAL_PORT}")
    say(f"backend:  http://localhost:{BACKEND_PORT}/health")
    say(f"judge:    {'http' if want_local else 'https'}://localhost:{LOCAL_PORT if want_local else PHONE_PORT}/judge/")
    if args.check:
        return 0

    for port, label in ((BACKEND_PORT, "backend"), (PHONE_PORT, "phone client"), (LOCAL_PORT, "local client")):
        if not port_free(port) and (port == BACKEND_PORT or (port == PHONE_PORT and want_phone) or (port == LOCAL_PORT and want_local)):
            fail(f"Port {port} ({label}) is already in use. Stop the other server and retry.")

    # --- start ---------------------------------------------------------
    processes: list[subprocess.Popen] = []
    try:
        print("\nStarting the backend (loads the detector; ~30 s the first time)...")
        processes.append(start_backend())
        if want_phone:
            processes.append(start_client(node_dir, plain_http=False))
        if want_local:
            processes.append(start_client(node_dir, plain_http=True))

        if not wait_for_port(BACKEND_PORT, timeout_s=300):
            fail("The backend did not come up within five minutes; see its output above.")
        say("Backend ready")

        if want_local:
            wait_for_port(LOCAL_PORT, timeout_s=60)
            webbrowser.open(f"http://localhost:{LOCAL_PORT}/")
        print("\nRunning. Press Ctrl+C to stop everything.\n")

        while True:
            for proc in processes:
                if proc.poll() is not None:
                    fail(f"A server exited with code {proc.returncode}; see its output above.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
