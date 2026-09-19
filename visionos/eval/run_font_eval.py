"""Score the reader by typeface, with everything else held constant.

    cd visionos && PYTHONPATH=. .venv/bin/python eval/run_font_eval.py

Uses the font benchmark in fonts.py: flat, evenly lit, one size, and only the
face varies. Families whose fonts are not installed on this machine are
skipped; the bundled families from typefaces.py exist everywhere, so those
rows compare across machines.
"""

from __future__ import annotations

import platform
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import cer, normalize_output
from fonts import build_font_corpus

from backend.ai.ocr import build_reader, format_for_speech


def main() -> int:
    ocr = build_reader()
    if not ocr.available:
        print("No OCR engine available.")
        return 1
    ocr.warmup()

    samples = build_font_corpus()
    if not samples:
        print("No fonts from the benchmark exist on this machine.")
        return 1

    by_family: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    for sample in samples:
        spoken = normalize_output(format_for_speech(ocr.read_consensus_sync(sample["frames"])))
        by_family[sample["family"]].append((sample["font"], sample["truth"], cer(spoken, sample["truth"])))
        if spoken.lower().strip() != sample["truth"].lower().strip():
            print(f"   {sample['family']:<18} {sample['font'][:22]:<22} want={sample['truth']!r:<12} got={spoken[:36]!r}")

    print(f"\n{'=' * 72}\nFONT EVAL  (engine={ocr.name}, {platform.system()}, n={len(samples)})\n{'=' * 72}")
    print(f"  {'family':<18} {'n':>3}  {'exact':>6}  {'CER':>6}")
    for family, rows in by_family.items():
        exact = sum(1 for _, _, error in rows if error == 0.0)
        mean = sum(error for _, _, error in rows) / len(rows)
        print(f"  {family:<18} {len(rows):>3}  {exact / len(rows) * 100:>5.0f}%  {mean:>6.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
