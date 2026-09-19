"""Score the OCR pipeline. Run this before and after any change to it.

    cd visionos && PYTHONPATH=. .venv/bin/python eval/run_ocr_eval.py 60
    cd visionos && PYTHONPATH=. .venv/bin/python eval/run_ocr_eval.py 60 --fonts bundled

Reports character error rate by apparent text size, plus how often the reader
invents text on surfaces that contain none. Both numbers matter: a reader that
says nothing is unhelpful, one that speaks confident nonsense is misleading,
and a blind user cannot tell the difference by glancing at the sign.

Do not replace CER with a "does the output contain the word" check. That check
passes "Room 204B" read as "Room 2048", which is how the first version of this
pipeline shipped looking fine and performed badly.

The engine under test is whichever `build_reader()` picks on this machine --
Apple Vision on macOS, RapidOCR elsewhere -- so numbers are comparable only
across runs on the same engine and font set. The report names both.
"""

from __future__ import annotations

import platform
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import build_corpus, build_textureless_corpus, cer, normalize_output
from typefaces import font_set

from backend.ai.ocr import TextReader, build_reader, format_for_speech


def evaluate(ocr: TextReader, samples, label: str):
    rows = []
    for sample in samples:
        spoken = format_for_speech(ocr.read_consensus_sync(sample.frames))
        prediction = normalize_output(spoken)
        rows.append((sample, prediction, cer(prediction, sample.truth)))

    exact = sum(
        1 for s, p, _ in rows if p.lower().strip() == s.truth.lower().strip()
    )
    silent = sum(1 for _, p, _ in rows if not p or p.startswith("I don't"))
    mean_cer = sum(c for _, _, c in rows) / len(rows)

    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
    print(f"  mean CER      {mean_cer:.3f}   (0 = perfect)")
    print(f"  exact match   {exact}/{len(rows)}  ({exact / len(rows) * 100:.0f}%)")
    print(f"  silent        {silent}/{len(rows)}  ({silent / len(rows) * 100:.0f}%)")

    by_size = defaultdict(list)
    for sample, _, error in rows:
        by_size[sample.height_pct].append(error)

    print(f"\n  {'text height':>12}  {'n':>3}  {'CER':>6}")
    for pct in sorted(by_size):
        values = by_size[pct]
        print(f"  {pct * 100:>11.1f}%  {len(values):>3}  {sum(values) / len(values):>6.3f}")

    return rows, mean_cer


def evaluate_hallucination(ocr: TextReader, count: int = 8):
    """Surfaces with no text. Anything spoken here is invented."""
    samples = build_textureless_corpus(count)
    spoke = 0
    for frames in samples:
        spoken = format_for_speech(ocr.read_consensus_sync(frames))
        if not spoken.startswith("I don't"):
            spoke += 1
            print(f"    invented: {spoken[:60]!r}")

    print(f"\n  hallucinated  {spoke}/{len(samples)}  (0 is the only acceptable score)")
    return spoke


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Score the OCR pipeline on signage.")
    parser.add_argument("count", nargs="?", type=int, default=60)
    parser.add_argument(
        "--fonts", choices=("platform", "bundled", "all"), default="platform",
        help="platform: this machine's system fonts (default); bundled: the "
             "display faces committed under eval/fonts/, identical everywhere",
    )
    args = parser.parse_args()
    count = args.count
    fonts = font_set(args.fonts)

    ocr = build_reader()
    if not ocr.available:
        print("No OCR engine available: install pyobjc-framework-Vision (macOS) "
              "or rapidocr-onnxruntime.")
        return 1
    ocr.warmup()

    import time

    label = (
        f"OCR EVAL  (n={count}, engine={ocr.name}, {platform.system()}, "
        f"{args.fonts} fonts: {len(fonts)})"
    )
    samples = build_corpus(count, fonts=fonts)  # rendering is slow; keep it out of the timing
    started = time.perf_counter()
    rows, mean_cer = evaluate(ocr, samples, label)
    elapsed = time.perf_counter() - started
    print(f"  read time     {elapsed / count * 1000:.0f} ms mean per sample (3-frame burst)")

    print(f"\n{'=' * 72}\nNO-TEXT SURFACES\n{'=' * 72}")
    evaluate_hallucination(ocr)

    print("\n  worst cases:")
    for sample, prediction, error in sorted(rows, key=lambda r: -r[2])[:10]:
        print(
            f"   CER {error:.2f}  {sample.height_pct * 100:>4.1f}%  "
            f"{sample.font[:20]:<20} want={sample.truth!r:<22} got={prediction[:32]!r}"
        )

    print(f"\n  reference (apple-vision, macOS fonts): CER 0.182, exact 70%, silent 2% as of 2026-09-19")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
