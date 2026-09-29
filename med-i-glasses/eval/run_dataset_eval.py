"""Run the app's reader over a folder of the shared dataset and score it.

    cd med-i-glasses
    PYTHONPATH=. .venv/bin/python eval/run_dataset_eval.py ../dataset/real
    PYTHONPATH=. .venv/bin/python eval/run_dataset_eval.py ../dataset/out --task medicine

The folder is anything in the `dataset/` layout: `<task>/labels.jsonl` with
frames beside it. `dataset/real` holds real photographs (dataset/fetch_real.py),
`dataset/out` the synthetic corpora (dataset/generate.py). Every sample is
read the way Read reads (consensus over its frames, then what would be
spoken), the allergen matcher runs on the lines for the allergen tasks, and
the predictions are scored by dataset/score.py, so any other system scored
with that file compares with this one.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import normalize_output

from backend.ai.ocr import build_reader, format_for_speech
from backend.alerts.allergy import find_allergen_mentions

DATASET = Path(__file__).resolve().parents[2] / "dataset"
sys.path.insert(0, str(DATASET))

from score import load_labels, score

PROFILE = ["peanut", "tree nut", "dairy", "egg", "gluten", "shellfish", "fish", "soy", "sesame"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--task", action="append", help="only these tasks (repeatable)")
    parser.add_argument("--out", type=Path, default=None, help="predictions file to write")
    args = parser.parse_args()

    reader = build_reader()
    if not reader.available:
        print("No OCR engine available.")
        return 1
    reader.warmup()
    engine = reader.name + (f" ({Path(reader.rec_model).name})" if getattr(reader, "rec_model", None) else "")
    print(f"engine={engine} on {platform.system()}  folder={args.folder}")

    labels = load_labels(args.folder)
    predictions: dict[str, dict] = {}
    started = time.perf_counter()
    for sample_id, row in labels.items():
        if args.task and row["task"] not in args.task:
            continue
        frames = [args.folder / row["task"] / f for f in row["frames"]]
        if not all(f.exists() for f in frames):
            continue
        lines = reader.read_consensus_sync([f.read_bytes() for f in frames])
        spoken = normalize_output(format_for_speech(lines))
        prediction = {"id": sample_id, "text": "" if spoken.startswith("I don't") else spoken}
        if row["task"] in ("allergens", "ingredients"):
            mentions = [m for m in find_allergen_mentions(lines, PROFILE) if m.statement]
            if row["task"] == "ingredients":
                # Open Food Facts lists what a product contains; traces are
                # a separate list, so a hedged mention is not a claim here.
                mentions = [m for m in mentions if not m.hedged]
            prediction["allergens"] = sorted({m.allergen for m in mentions})
        predictions[sample_id] = prediction
        print(f"\r  {len(predictions)} read", end="", flush=True)
    elapsed = time.perf_counter() - started
    print(f"\r  {len(predictions)} samples, {elapsed / max(1, len(predictions)):.1f} s each")

    if args.out:
        args.out.write_text("".join(json.dumps(p) + "\n" for p in predictions.values()), encoding="utf-8")
    for key, metrics in score(labels, predictions).items():
        print(f"  {key:<22} " + "  ".join(f"{k}={v}" for k, v in metrics.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
