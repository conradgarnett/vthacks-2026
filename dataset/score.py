"""Score a system's predictions against the dataset. Standard library only.

    python dataset/score.py dataset/out predictions.jsonl

predictions.jsonl has one line per sample you ran:

    {"id": "signage-004", "text": "Room 204B"}
    {"id": "notext-symbol-002", "text": ""}
    {"id": "allergen-011", "text": "...", "allergens": ["peanut"]}

`text` is what your system would SAY for that sample, after any wrapper
("It reads: ...") is removed. An empty string means it stayed silent.
`allergens` is optional and only read for the allergens task: the groups
your system would ACT on (report as present). Leave it out to score reading
alone.

Metrics, and why each exists:

  CER            character error rate, capped at 1. Not "does the output
                 contain the word": that check passes "Room 204B" read as
                 "Room 2048", which is how a pipeline once looked fine while
                 performing badly.
  exact          whole-string match, case-insensitive.
  silent         said nothing. Unhelpful, but safe.
  invented       said something where there is nothing to say (no_text), or
                 reported an allergen that is not on the label. For a blind
                 user this is worse than silence: they cannot check it.
  dose wrong     on a prescription label, the number after TAKE came out
                 different. The one error that can hurt someone. Kept apart
                 from "not read", which is safe.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(pred: str, truth: str) -> float:
    if not truth:
        return 0.0
    return min(1.0, edit_distance(pred.lower().strip(), truth.lower().strip()) / len(truth))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# The dose is the number immediately after "take"; "TAKE 1 TABLET THREE TIMES
# DAILY" has dose 1 and frequency 3.
_DOSE = re.compile(r"\btake\s+(?:about\s+)?(\d+|half|one|two|three|four)\b", re.IGNORECASE)
_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4"}


def _dose(text: str) -> str | None:
    m = _DOSE.search(text)
    return None if not m else _WORDS.get(m.group(1).lower(), m.group(1).lower())


def load_labels(root: Path) -> dict[str, dict]:
    labels = {}
    for path in sorted(root.glob("*/labels.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                labels[row["id"]] = row
    return labels


def score(labels: dict[str, dict], preds: dict[str, dict]) -> dict[str, dict]:
    groups: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for sample_id, pred in preds.items():
        if sample_id in labels:
            row = labels[sample_id]
            groups[f"{row['task']}/{row['split']}"].append((row, pred))

    report: dict[str, dict] = {}
    for key, pairs in sorted(groups.items()):
        task = key.split("/")[0]
        n = len(pairs)
        said = [(row, (p.get("text") or "").strip()) for row, p in pairs]
        out: dict = {"n": n}
        if task in ("signage", "packaging", "fonts"):
            out["mean_cer"] = round(sum(cer(t, r["text"]) for r, t in said) / n, 3)
            out["exact"] = sum(t.lower() == r["text"].lower() for r, t in said)
            out["silent"] = sum(not t for _, t in said)
            if task == "packaging":
                out["name_present"] = sum(r["text"].lower() in t.lower() for r, t in said)
        elif task == "no_text":
            out["invented"] = sum(bool(t) for _, t in said)
        elif task == "medicine":
            out["drug_found"] = sum(_norm(r["drug"]) in _norm(t) for r, t in said)
            out["strength_found"] = sum(_norm(r["strength"]) in _norm(t) for r, t in said)
            right = wrong = silent = 0
            for r, t in said:
                got, want = _dose(_norm(t)), _dose(r["directions"])
                if got is None:
                    silent += 1
                elif got == want:
                    right += 1
                else:
                    wrong += 1
            out.update(dose_right=right, dose_WRONG=wrong, dose_not_read=silent)
        elif task == "ingredients":
            # Real panels: the reference is another engine's reading, not
            # exact truth, so reading is scored as the share of the
            # ingredient list's words recovered, not as CER.
            recall = []
            for r, t in said:
                want = {w for span in r["ingredients"] for w in _norm(span).split() if len(w) >= 3}
                got = set(_norm(t).split())
                if want:
                    recall.append(len(want & got) / len(want))
            out["ingredient_word_recall"] = round(sum(recall) / len(recall), 3) if recall else None
            out["silent"] = sum(not t for _, t in said)
            out.update(_allergen_counts(pairs))
        elif task == "allergens":
            out["statement_read_cer"] = round(
                sum(cer(t, r["statement"]) for r, t in said) / n, 3)
            out.update(_allergen_counts(pairs))
        report[key] = out
    return report


def _allergen_counts(pairs) -> dict:
    """Missed and invented kept apart; only samples whose prediction says
    which allergens the system would act on are counted."""
    acted = [(row, p) for row, p in pairs if "allergens" in p]
    if not acted:
        return {}
    truth_n = found = missed = invented = 0
    for row, p in acted:
        truth, got = set(row["allergens"]), set(p["allergens"])
        truth_n += len(truth)
        found += len(truth & got)
        missed += len(truth - got)
        invented += len(got - truth)
    return dict(allergens_on_labels=truth_n, reported=found, MISSED=missed, INVENTED=invented)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    labels = load_labels(Path(argv[1]))
    preds = {}
    for line in Path(argv[2]).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            preds[row["id"]] = row
    unknown = [k for k in preds if k not in labels]
    if unknown:
        print(f"warning: {len(unknown)} prediction ids not in the dataset, e.g. {unknown[0]}")
    for key, metrics in score(labels, preds).items():
        print(f"{key:<22} " + "  ".join(f"{k}={v}" for k, v in metrics.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
