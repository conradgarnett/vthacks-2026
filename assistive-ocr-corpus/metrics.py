"""Scoring for assistive text-reading, engine-agnostic.

Two metrics and one anti-metric.

CHARACTER ERROR RATE is the headline. Do not replace it with "does the output
contain the expected word": that check passes "Room 204B" read as "Room 2048",
and a reader can look healthy by it while performing badly in the hand. This
corpus exists partly because that mistake was made and measured.

HALLUCINATION RATE is scored on inputs containing no text at all -- carpet,
brick, foliage, blinds, barcodes, nutrition rings, decorative rules. It is not
a secondary metric. For a blind user, a reader that invents an ingredient is
worse than one that says nothing, because there is no way to check it. Report
it next to recall, always, and treat a rise in it as a regression even when
recall improved.

SILENCE is tracked separately from error, because the two have different costs
here. A reader that declines is unhelpful; a reader that is confidently wrong
is misinformation. Any system built on this corpus should be able to show that
it moved silence down without moving hallucination up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, iterative and allocation-light."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def cer(prediction: str, truth: str) -> float:
    """Character error rate, capped at 1.0. Lower is better.

    Capped so one catastrophic sample cannot dominate a mean: an unbounded CER
    lets a reader that emitted a paragraph of noise on one image outweigh
    twenty correct reads.
    """
    prediction = prediction.lower().strip()
    truth = truth.lower().strip()
    if not truth:
        return 0.0
    return min(1.0, edit_distance(prediction, truth) / len(truth))


def flatten(text: str) -> str:
    """Strip everything but alphanumerics, for containment checks."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def normalize(text: str) -> str:
    """Collapse punctuation and case, keeping word boundaries."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass(slots=True)
class Result:
    """One corpus scored. `hallucinated` is over no-text inputs only."""

    samples: int
    mean_cer: float
    exact: int
    silent: int
    hallucinated: int
    no_text_samples: int

    @property
    def exact_rate(self) -> float:
        return self.exact / self.samples if self.samples else 0.0

    @property
    def silent_rate(self) -> float:
        return self.silent / self.samples if self.samples else 0.0

    @property
    def hallucination_rate(self) -> float:
        return (
            self.hallucinated / self.no_text_samples if self.no_text_samples else 0.0
        )

    def report(self, label: str = "") -> str:
        head = f"{label}  n={self.samples}" if label else f"n={self.samples}"
        return (
            f"{head}\n"
            f"  mean CER        {self.mean_cer:.3f}   (0 = perfect)\n"
            f"  exact match     {self.exact}/{self.samples}"
            f"  ({self.exact_rate * 100:.0f}%)\n"
            f"  silent          {self.silent}/{self.samples}"
            f"  ({self.silent_rate * 100:.0f}%)\n"
            f"  hallucinated    {self.hallucinated}/{self.no_text_samples}"
            f"  (0 is the only acceptable score)"
        )


def score(
    predictions: list[str],
    truths: list[str],
    no_text_predictions: list[str] | None = None,
    silent_marker: str = "",
) -> Result:
    """Score one corpus.

    `predictions` and `truths` are aligned. `no_text_predictions` are this
    reader's output on inputs that contain no text; anything non-empty there
    was invented. `silent_marker` is whatever your reader emits when it
    declines -- pass it so a refusal is counted as silence rather than as a
    wrong answer, which is the distinction this corpus cares about.
    """
    if len(predictions) != len(truths):
        raise ValueError(
            f"{len(predictions)} predictions against {len(truths)} truths"
        )

    def is_silent(text: str) -> bool:
        stripped = text.strip()
        return not stripped or (bool(silent_marker) and silent_marker in stripped)

    errors = [cer(p, t) for p, t in zip(predictions, truths)]
    exact = sum(
        1 for p, t in zip(predictions, truths)
        if p.lower().strip() == t.lower().strip()
    )
    silent = sum(1 for p in predictions if is_silent(p))

    no_text = no_text_predictions or []
    invented = sum(1 for p in no_text if not is_silent(p))

    return Result(
        samples=len(predictions),
        mean_cer=sum(errors) / len(errors) if errors else 0.0,
        exact=exact,
        silent=silent,
        hallucinated=invented,
        no_text_samples=len(no_text),
    )
