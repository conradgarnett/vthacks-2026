"""Score prescription-label reading, field by field.

    cd visionos && PYTHONPATH=..:. .venv/bin/python eval/run_medicine_eval.py

Fields are scored separately because they are not equally important. A label
whose drug name reads perfectly and whose directions read wrong is a failure,
not a partial success -- and the dosage NUMBER is the field that can hurt
someone, so it is scored on its own and a silent miss is counted apart from a
wrong answer.

Reports a tuning set and a held-out set generated from a disjoint seed range.
A large gap between them means the thresholds have been fitted to the corpus
rather than to reading.
"""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medicine import build_holdout_corpus, build_medicine_corpus

from backend.ai.ocr import build_reader, format_for_speech

# The dose is the number *immediately* after "take". Searching the whole
# sentence conflates it with the frequency: "TAKE 1 TABLET THREE TIMES
# DAILY" has dose 1 and frequency 3, and a looser pattern scored that as a
# wrong dose when the reader had actually refused to state one.
_NUM = re.compile(
    r"\btake\s+(?:about\s+)?(\d+|half|one|two|three|four)\b", re.IGNORECASE
)
_WORD_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "half": "half"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _dose_number(text: str) -> str | None:
    """The quantity in 'TAKE 2 TABLETS'. The field that can cause harm."""
    match = _NUM.search(text)
    if not match:
        return None
    token = match.group(1).lower()
    return _WORD_NUM.get(token, token)


def score(ocr, samples, label: str) -> dict:
    stats = {k: 0 for k in
             ("drug", "strength", "dir_full", "dose_right", "dose_wrong", "dose_silent")}

    for s in samples:
        spoken = _norm(format_for_speech(ocr.read_consensus_sync(s["frames"])))

        if _norm(s["drug"]) in spoken:
            stats["drug"] += 1
        if _norm(s["strength"]) in spoken:
            stats["strength"] += 1

        directions = _norm(s["directions"])
        if directions in spoken:
            stats["dir_full"] += 1

        want = _dose_number(s["directions"])
        # Matched against the whole output, because the pattern itself is
        # anchored on "take" and will not pick up a refill or quantity.
        got = _dose_number(spoken)
        if got is None:
            stats["dose_silent"] += 1
        elif got == want:
            stats["dose_right"] += 1
        else:
            stats["dose_wrong"] += 1

    n = len(samples)
    pct = lambda k: stats[k] / n * 100
    print(f"\n{'=' * 66}\n{label}   n={n}\n{'=' * 66}")
    print(f"  drug name found          {pct('drug'):>5.0f}%")
    print(f"  strength found           {pct('strength'):>5.0f}%")
    print(f"  full directions exact    {pct('dir_full'):>5.0f}%")
    print("  --- dosage number (safety critical) ---")
    print(f"  correct                  {pct('dose_right'):>5.0f}%")
    print(f"  WRONG                    {pct('dose_wrong'):>5.0f}%   <- must be 0")
    print(f"  not read (safe)          {pct('dose_silent'):>5.0f}%")
    return {k: stats[k] / n for k in stats}


def main() -> int:
    ocr = build_reader()
    if not ocr.available:
        print("No OCR engine available.")
        return 1
    ocr.warmup()
    print(f"engine={ocr.name} on {platform.system()}")

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    tune = score(ocr, build_medicine_corpus(n), "TUNING SET")
    held = score(ocr, build_holdout_corpus(n), "HELD-OUT SET (never tuned on)")

    gap = tune["drug"] - held["drug"]
    print(f"\n  overfitting check: drug-name gap {gap * 100:+.0f} points")
    print("  a large positive gap means thresholds are fitted to the tuning corpus")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
