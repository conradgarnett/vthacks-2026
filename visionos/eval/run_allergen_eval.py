"""Score the allergen statement path: what it misses, and what it invents.

    cd visionos && PYTHONPATH=. .venv/bin/python eval/run_allergen_eval.py

The two failures are not symmetric and the report keeps them apart:

  MISSED     an allergen on the label that the ladder never reported. The
             wearer eats it.
  INVENTED   an allergen reported that is not on the label. This one emails
             a doctor about a peanut that was a chickpea, and a few of those
             teach the wearer to ignore every alert afterwards. Wants zero.

Scored end to end -- render, photograph, read, then run the real matcher
(backend/alerts/allergy.find_allergen_mentions) over the lines the reader
recovered. A corpus that scored the matcher on clean strings would measure
nothing: the whole difficulty is that these lines are small print seen
through curvature, glare and JPEG.

The corpus carries the cases that break naive matching -- negations
("DAIRY FREE"), hedges ("MAY CONTAIN"), product-name traps ("Peanut Butter
Cups"), plant milks ("ALMOND MILK") and derived names ("WHEY", "SEMOLINA").
See allergens.py.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from allergens import build_allergen_corpus

from backend.ai.ocr import TextReader, build_reader
from backend.alerts.allergy import find_allergen_mentions, has_statement

# The profile under test: everything the matcher knows, so nothing is missed
# for being unasked-for. A real wearer has fewer.
PROFILE = ["peanut", "tree nut", "dairy", "egg", "gluten", "shellfish", "fish", "soy", "sesame"]


def main() -> int:
    ocr: TextReader = build_reader()
    if not ocr.available:
        print("No OCR engine available.")
        return 1
    ocr.warmup()

    samples = build_allergen_corpus()
    print(f"engine={ocr.name} on {platform.system()}   n={len(samples)}")

    missed_total = invented_total = truth_total = found_total = 0
    silent = 0
    # Splits the blame: a statement line the reader never recovered is a
    # reading problem, not a matching one, and they have different fixes.
    statement_recovered = 0
    read_nothing = 0
    negation_failures: list[str] = []
    hedge_errors: list[str] = []
    per_case: list[tuple[str, set, set, bool]] = []

    for s in samples:
        # Consensus across the frames, which is what the ladder reads: the
        # email needs two frames that agree, so scoring a single frame would
        # measure something the scanner never does.
        lines = ocr.read_consensus_sync(s["frames"])

        if not lines:
            read_nothing += 1
        if has_statement(lines):
            statement_recovered += 1

        mentions = find_allergen_mentions(lines, PROFILE)
        # Only what the ladder would act on: a mention inside a statement.
        # A name on the front of the pack is not an ingredient list.
        reported = {m.allergen for m in mentions if m.statement}
        hedged_reported = {m.allergen for m in mentions if m.statement and m.hedged}

        truth = s["truth"]
        missed = truth - reported
        invented = reported - truth

        truth_total += len(truth)
        found_total += len(truth & reported)
        missed_total += len(missed)
        invented_total += len(invented)
        if truth and not reported:
            silent += 1
        # A negation sample has no truth: anything reported came from a line
        # that says the allergen is ABSENT.
        if not truth and invented:
            negation_failures.append(f"{s['statement'][:52]} -> {sorted(invented)}")
        # A hedged statement must not arrive as a bare presence.
        if s["hedged"] and (reported - hedged_reported):
            hedge_errors.append(f"{s['statement'][:52]} -> unhedged {sorted(reported - hedged_reported)}")

        per_case.append((s["statement"], truth, reported, s["hedged"]))

    print()
    print("=" * 66)
    print("ALLERGEN STATEMENTS")
    print("=" * 66)
    recall = found_total / truth_total if truth_total else 0.0
    print(f"  allergens on the labels    {truth_total}")
    print(f"  reported correctly         {found_total}  ({recall:.0%})")
    print(f"  MISSED                     {missed_total}")
    print(f"  INVENTED                   {invented_total}   (0 is the only acceptable score)")
    print(f"  labels with a statement    {silent} read silently")
    print()
    print(f"  reader recovered a statement line   {statement_recovered}/{len(samples)}")
    print(f"  reader recovered nothing at all     {read_nothing}/{len(samples)}")
    print()
    print(f"  negations mishandled       {len(negation_failures)}")
    for line in negation_failures:
        print(f"      {line}")
    print(f"  hedges reported as certain {len(hedge_errors)}")
    for line in hedge_errors:
        print(f"      {line}")

    print()
    print("  per statement (truth -> reported)")
    for statement, truth, reported, hedged in per_case:
        mark = "ok " if truth == reported else ("INV" if reported - truth else "mis")
        tag = " hedged" if hedged else ""
        print(f"    {mark} {statement[:46]:<48}{sorted(truth)} -> {sorted(reported)}{tag}")

    return 1 if invented_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
