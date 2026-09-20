"""Score the reader on packaging and on symbol-only images.

    cd med-i-glasses && PYTHONPATH=. .venv/bin/python eval/run_packaging_eval.py

Two corpora from packaging.py. Product labels: the truth is the product name,
scored by character error rate on the spoken output, and separately whether
the name appears at all (the ingredient small print is long and correct, and
can bury it). Symbol-only images: barcodes, recycling marks, nutrition badges,
scrollwork, rules. Nothing spoken there is real; every line is invented, and
a blind user cannot glance at the can to find out.

The engine is whatever build_reader() picks. The report names it, and for
engines whose confidence carries information it prints the score
distribution of what was kept versus what was invented.
"""

from __future__ import annotations

import platform
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import cer, normalize_output
from packaging import build_packaging_corpus, build_symbol_corpus

from backend.ai.ocr import TextReader, build_reader, format_for_speech


def main() -> int:
    ocr: TextReader = build_reader()
    if not ocr.available:
        print("No OCR engine available.")
        return 1
    ocr.warmup()
    print(f"engine={ocr.name} on {platform.system()}")

    # --- packaging ------------------------------------------------------
    samples = build_packaging_corpus()
    rows = []
    for sample in samples:
        lines = ocr.read_consensus_sync(sample["frames"])
        spoken = normalize_output(format_for_speech(lines))
        truth = sample["truth"]
        present = truth.lower() in spoken.lower()
        rows.append((sample, spoken, cer(spoken, truth), present, lines))

    present = sum(1 for *_, p, _ in rows if p)
    first = sum(
        1 for sample, spoken, _, _, _ in rows
        if spoken.lower().startswith(sample["truth"].lower())
    )
    print(f"\n{'=' * 72}\nPACKAGING  (n={len(rows)})\n{'=' * 72}")
    print(f"  product name present   {present}/{len(rows)}  ({present / len(rows) * 100:.0f}%)")
    print(f"  product name spoken first  {first}/{len(rows)}  ({first / len(rows) * 100:.0f}%)")
    print(f"  mean CER vs name only  {sum(c for _, _, c, _, _ in rows) / len(rows):.3f}  (small print counts against it)")
    curved = [r for r in rows if r[0]["curved"]]
    flat = [r for r in rows if not r[0]["curved"]]
    if curved and flat:
        print(f"  name present, curved   {sum(1 for r in curved if r[3])}/{len(curved)}")
        print(f"  name present, flat     {sum(1 for r in flat if r[3])}/{len(flat)}")
    print("\n  misses:")
    for sample, spoken, error, was_present, _ in rows:
        if not was_present:
            print(f"   {sample['font'][:22]:<22} want={sample['truth']!r:<18} got={spoken[:48]!r}")

    kept_scores = [line.confidence for *_, lines in rows for line in lines]

    # --- symbols --------------------------------------------------------
    symbols = build_symbol_corpus()
    invented = 0
    invented_scores: list[float] = []
    print(f"\n{'=' * 72}\nSYMBOLS ONLY  (n={len(symbols)})\n{'=' * 72}")
    for sample in symbols:
        lines = ocr.read_consensus_sync(sample["frames"])
        spoken = format_for_speech(lines)
        if not spoken.startswith("I don't"):
            invented += 1
            invented_scores.extend(line.confidence for line in lines)
            print(f"   {sample['kind']:<10} invented: {spoken[:60]!r}")
    print(f"\n  hallucinated  {invented}/{len(symbols)}  (0 is the only acceptable score)")

    if kept_scores:
        print(f"\n  engine confidence, lines spoken on packaging: "
              f"median {statistics.median(kept_scores):.2f}, min {min(kept_scores):.2f}")
    if invented_scores:
        print(f"  engine confidence, lines invented on symbols:  "
              f"median {statistics.median(invented_scores):.2f}, max {max(invented_scores):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
