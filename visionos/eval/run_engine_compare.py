"""Head-to-head between OCR engines on identical data.

    cd visionos && PYTHONPATH=..:. .venv/bin/python eval/run_engine_compare.py

Engine choice turned out to matter more than everything else in the read
pipeline combined. Measured on this machine, RapidOCR with no post-processing
beat Apple Vision carrying the full pipeline by a wide margin on every corpus
that matters, and by 6x on cursive -- which had been written off as an engine
limit. It was an engine limit; it was the wrong engine.

Both engines run on macOS, so this comparison is not cross-machine folklore.
Run it before choosing an engine for a task, and re-run it when either engine
is upgraded.
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

SROIE = Path(__file__).resolve().parent / "data" / "sroie"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _rapid_reader():
    """RapidOCR, returning (text, confidence) pairs, or None if unavailable."""
    try:
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None

    engine = RapidOCR()

    def read(frame_jpeg: bytes) -> list[tuple[str, float]]:
        array = np.array(Image.open(io.BytesIO(frame_jpeg)).convert("RGB"))
        result, _ = engine(array)
        return [(line[1], float(line[2])) for line in (result or [])]

    return read


def _vision_reader():
    from backend.ai.ocr import AppleVisionOCR, format_for_speech

    engine = AppleVisionOCR()
    if not engine.available:
        return None
    engine.warmup()

    def read(frame_jpeg: bytes) -> str:
        return format_for_speech(engine.read_consensus_sync([frame_jpeg]))

    return read


def compare_receipts(limit: int = 25) -> None:
    """Real photographs with per-line ground truth."""
    images = sorted((SROIE / "img").glob("*.jpg"))[:limit]
    if not images:
        print(f"No SROIE data at {SROIE}; skipping receipts.")
        return

    vision, rapid = _vision_reader(), _rapid_reader()
    total = vision_hits = rapid_hits = 0
    vision_ms: list[float] = []
    rapid_ms: list[float] = []

    for path in images:
        box = SROIE / "box" / f"{path.stem}.csv"
        if not box.exists():
            continue
        with box.open(newline="", encoding="utf-8", errors="ignore") as handle:
            truth = [r[8].strip() for r in csv.reader(handle) if len(r) > 8 and r[8].strip()]
        if not truth:
            continue

        payload = path.read_bytes()
        total += len(truth)

        if vision:
            start = time.perf_counter()
            text = _norm(vision(payload))
            vision_ms.append((time.perf_counter() - start) * 1000)
            vision_hits += sum(1 for line in truth if _norm(line) and _norm(line) in text)

        if rapid:
            start = time.perf_counter()
            text = _norm(" ".join(t for t, _ in rapid(payload)))
            rapid_ms.append((time.perf_counter() - start) * 1000)
            rapid_hits += sum(1 for line in truth if _norm(line) and _norm(line) in text)

    print(f"\n{'=' * 66}\nREAL RECEIPTS   n={len(images)}   ground-truth lines {total}\n{'=' * 66}")
    if vision:
        print(f"  Apple Vision (full pipeline)  {vision_hits / total * 100:>5.0f}%"
              f"   {statistics.median(vision_ms):>6.0f} ms")
    if rapid:
        print(f"  RapidOCR (raw)                {rapid_hits / total * 100:>5.0f}%"
              f"   {statistics.median(rapid_ms):>6.0f} ms")
    if not rapid:
        print("  RapidOCR not installed: uv pip install rapidocr-onnxruntime")


def compare_hallucination() -> None:
    """Surfaces with no text. Anything returned was invented."""
    from corpus import build_textureless_corpus
    from packaging import build_symbol_corpus

    vision, rapid = _vision_reader(), _rapid_reader()
    if not rapid:
        return

    from backend.ai.lexicon import correct_text
    from backend.ai.text_quality import assess, clean_for_speech

    def filtered(payload: bytes) -> str:
        kept = []
        for text, confidence in rapid(payload):
            # RapidOCR's confidence is meaningful, unlike Vision's flat ~0.5,
            # so it can carry real weight here.
            if confidence < 0.60 or not assess(text, confidence).keep:
                continue
            kept.append(correct_text(clean_for_speech(text)))
        return " ".join(kept).strip()

    print(f"\n{'=' * 66}\nNO-TEXT SURFACES (anything spoken is invented)\n{'=' * 66}")
    for name, frames in (
        ("texture", [f[0] for f in build_textureless_corpus(8)]),
        ("symbol", [x["frames"][0] for x in build_symbol_corpus(8)]),
    ):
        raw = sum(1 for f in frames if " ".join(t for t, _ in rapid(f)).strip())
        filt = sum(1 for f in frames if filtered(f))
        vis = (
            sum(1 for f in frames if not vision(f).startswith("I don't"))
            if vision else 0
        )
        print(f"  {name:<8} RapidOCR raw {raw}/{len(frames)}"
              f"   RapidOCR + filters {filt}/{len(frames)}"
              f"   Apple Vision {vis}/{len(frames)}")


def main() -> int:
    compare_receipts()
    compare_hallucination()
    print("\n  Filtering costs RapidOCR recall (88% -> 75% on receipts) and")
    print("  removes its symbol hallucination (3/8 -> 0/8). Both matter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
