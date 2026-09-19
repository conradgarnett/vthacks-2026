"""Score the reader against REAL photographs, not synthetic renders.

    cd visionos && PYTHONPATH=..:. .venv/bin/python eval/run_sroie_eval.py

Every other corpus here is generated: I render text, then simulate a camera.
That is reproducible and lets a single variable be isolated, but it cannot
tell me whether the simulation resembles reality. If my blur, glare and
compression are wrong, every threshold tuned against them is tuned to a
fiction.

SROIE is 626 photographs of real receipts with per-line ground truth. Receipts
are the closest public analogue to a pharmacy label: dense small print, thermal
or laser type, creased and curled paper, shot handheld. A large gap between
this and the synthetic scores means the synthetic degradation is wrong.

Data is downloaded, not committed -- it is third-party and gitignored.
"""

from __future__ import annotations

import csv
import platform
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.ai.ocr import build_reader, format_for_speech

DATA = Path(__file__).resolve().parent / "data" / "sroie"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def ground_truth(path: Path) -> list[str]:
    lines = []
    with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
        for row in csv.reader(handle):
            if len(row) > 8 and row[8].strip():
                lines.append(row[8].strip())
    return lines


def main() -> int:
    ocr = build_reader()
    if not ocr.available:
        print("No OCR engine available.")
        return 1
    ocr.warmup()
    print(f"engine={ocr.name} on {platform.system()}")

    images = sorted((DATA / "img").glob("*.jpg"))
    if not images:
        print(f"No data at {DATA}. Download the SROIE sample first.")
        return 1

    total_lines = found_lines = 0
    per_image = []

    for image_path in images:
        box_path = DATA / "box" / f"{image_path.stem}.csv"
        if not box_path.exists():
            continue
        truth = ground_truth(box_path)
        if not truth:
            continue

        spoken = _norm(format_for_speech(ocr.read_consensus_sync([image_path.read_bytes()])))
        hits = sum(1 for line in truth if _norm(line) and _norm(line) in spoken)

        total_lines += len(truth)
        found_lines += hits
        per_image.append(hits / len(truth))

    coverage = found_lines / total_lines * 100 if total_lines else 0
    per_image.sort()
    median = per_image[len(per_image) // 2] * 100 if per_image else 0

    print(f"\n{'=' * 66}\nREAL PHOTOGRAPHS (SROIE receipts)   n={len(per_image)}\n{'=' * 66}")
    print(f"  ground-truth lines        {total_lines}")
    print(f"  lines recovered exactly   {found_lines}  ({coverage:.0f}%)")
    print(f"  median per-receipt        {median:.0f}%")
    print(f"  worst / best              {per_image[0] * 100:.0f}% / {per_image[-1] * 100:.0f}%")
    print("\n  Receipts are the closest public analogue to a pharmacy label:")
    print("  dense small print, creased paper, handheld capture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
