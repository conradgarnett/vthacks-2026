"""How much accuracy the camera costs, on the labels that matter.

    cd med-i-glasses && PYTHONPATH=. .venv/bin/python eval/run_camera_eval.py

Scores prescription labels and real receipts through three capture paths --
phone, decent webcam, cheap fixed-focus webcam -- so the camera's contribution
is a number rather than an assumption.

If `cheap` is far below `phone`, the fix is a better camera, and that is worth
knowing before spending a day on the reader. If they are close, the camera is
not the bottleneck and the reader is.
"""

from __future__ import annotations

import csv
import io
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from medicine import build_holdout_corpus, render_label, _spec
from webcam import TIERS, burst

from backend.ai.ocr import build_reader, format_for_speech

SROIE = Path(__file__).resolve().parent / "data" / "sroie"

_DOSE = re.compile(r"\btake\s+(\d+|one|two|three|four|half)\b", re.IGNORECASE)
_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4"}


def _flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _spaced(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _dose(text: str) -> str | None:
    match = _DOSE.search(text)
    if not match:
        return None
    token = match.group(1).lower()
    return _WORDS.get(token, token)


def prescriptions(reader, tier: str, count: int = 16) -> dict:
    """Re-photograph the held-out labels through one camera."""
    import random

    rng = random.Random(90210)
    camera = TIERS[tier]
    found = dose_ok = dose_bad = 0
    times: list[float] = []

    for index in range(count):
        spec = _spec(rng)
        label = render_label(spec, rng)
        frames = burst(label, camera, frames=3, seed=1000 + index)

        started = time.perf_counter()
        spoken = _spaced(format_for_speech(reader.read_consensus_sync(frames)))
        times.append((time.perf_counter() - started) * 1000)

        if _spaced(spec["drug"]) in spoken:
            found += 1
        got, want = _dose(spoken), _dose(_spaced(spec["directions"]))
        if got is not None:
            if got == want:
                dose_ok += 1
            else:
                dose_bad += 1

    return {
        "drug": found / count * 100,
        "dose_ok": dose_ok / count * 100,
        "dose_wrong": dose_bad / count * 100,
        "ms": statistics.median(times),
    }


def receipts(reader, tier: str, limit: int = 12) -> dict:
    """Real receipt photographs, re-captured through one camera.

    A real photo passed through the camera model is the closest available
    thing to that receipt seen through the glasses.
    """
    camera = TIERS[tier]
    images = sorted((SROIE / "img").glob("*.jpg"))[:limit]
    if not images:
        return {}

    total = hits = 0
    times: list[float] = []
    for path in images:
        box = SROIE / "box" / f"{path.stem}.csv"
        if not box.exists():
            continue
        with box.open(newline="", encoding="utf-8", errors="ignore") as handle:
            truth = [r[8].strip() for r in csv.reader(handle) if len(r) > 8 and r[8].strip()]
        if not truth:
            continue

        photo = Image.open(io.BytesIO(path.read_bytes()))
        photo.load()
        frames = burst(photo, camera, frames=3, seed=hash(path.stem) % 9999)

        started = time.perf_counter()
        text = _flat(format_for_speech(reader.read_consensus_sync(frames)))
        times.append((time.perf_counter() - started) * 1000)

        total += len(truth)
        hits += sum(1 for line in truth if _flat(line) and _flat(line) in text)

    return {"lines": hits / total * 100 if total else 0, "ms": statistics.median(times)}


def main() -> int:
    reader = build_reader()
    if not reader.available:
        print("No OCR engine available.")
        return 1
    reader.warmup()

    print(f"\nengine: {reader.name}")
    print(f"\n{'camera':<9} {'sensor':>7} {'defocus':>8} | "
          f"{'presc drug':>11} {'dose ok':>8} {'WRONG':>6} {'ms':>6} | {'receipt':>8} {'ms':>6}")

    for tier in ("phone", "webcam", "cheap"):
        camera = TIERS[tier]
        p = prescriptions(reader, tier)
        r = receipts(reader, tier)
        print(
            f"{tier:<9} {camera.sensor_px:>6}px {camera.defocus_px:>7.1f}px | "
            f"{p['drug']:>10.0f}% {p['dose_ok']:>7.0f}% {p['dose_wrong']:>5.0f}% {p['ms']:>6.0f} | "
            f"{r.get('lines', 0):>7.0f}% {r.get('ms', 0):>6.0f}"
        )

    print("\n  A large phone-to-cheap gap means the camera is the bottleneck.")
    print("  wrong dose must stay 0% at every tier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
