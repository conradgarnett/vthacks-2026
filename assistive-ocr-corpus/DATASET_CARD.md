# Dataset card — assistive-ocr-corpus

## Summary

Seven procedurally generated corpora for evaluating text reading in assistive
use: pharmacy vials, food packaging, allergen statements, signage, a typeface
benchmark, and two corpora containing no text at all. Images are generated
from seeded code rather than stored, so the repository is ~6 MB of code and
typefaces instead of gigabytes of JPEG, and any given corpus is reproducible
byte-for-byte from its seed.

- **Version**: 1.0, 2026-09-29
- **Licence**: MIT for code and generated images; per-face licences for the
  bundled typefaces (`FONT_LICENSES.md`)
- **Language**: English, Latin script
- **Task**: OCR / scene text recognition, evaluation
- **Size**: 240 samples across all defaults, ~700 frames; unbounded, since
  `--n` and `--seed` generate more

## Motivation

Public OCR benchmarks measure documents. A blind user is not reading a
document — they are holding a curved amber vial at 20 cm under a fixed-focus
lens, and the field that matters (the directions) is printed smaller than the
brand name. No public dataset covers that, and no public dataset of real
prescription labels can exist, because a real label identifies a patient.

The corpora were built to answer questions the source project actually had to
decide, and each one exists because a decision turned on it:

- Does the reader invent words on a textured wall? (`textureless`, `symbols`)
- Are the thresholds fitted to the corpus? (`medicine` held-out split)
- Is a wrong dose ever spoken? (`medicine`, scored separately from recall)
- Is the limit the algorithm or the lens? (`webcam` tiers)
- Which typefaces fail, holding everything else constant? (`fonts`)

## Composition

| corpus | samples | frames each | ground truth |
|---|---|---|---|
| `signage` | 60 | 3 | phrase |
| `medicine` (tuning) | 40 | 3 | drug, strength, directions, warning |
| `medicine` (held out, seed 90210) | 40 | 3 | as above |
| `labels` | 24 | 3 | product name |
| `allergens` | 40 | 3 | statement text, allergen set, hedged flag |
| `fonts` | 76 | 3 | phrase, family |
| `textureless` | 8 | 3 | **none** |
| `symbols` | 12 | 3 | **none** |

Each sample is a burst of frames of one scene, differing only as consecutive
camera frames would. Frames within a burst are not independent samples.

Ground truth is exact by construction: the generator knows what it drew.
There is no annotation noise, which is a strength for measurement and a
weakness for realism — real annotation disagreement is part of why real
benchmarks are harder.

### What the held-out split actually holds out

`build_holdout_corpus()` draws from a seed range the tuning set never
touches, so the two sets share no label *instance* — layout, scale,
curvature, vial tint, sticker colour and capture are all independently drawn.
They do **not** hold out vocabulary: both draw drug names, pharmacies and
directions from the same short lists, so the same drug appears in both, and
that is expected.

The consequence is worth being precise about. This split detects **threshold
overfitting** — a confidence floor or a gate tuned until it happens to suit
one corpus. It does **not** detect **vocabulary memorization**, and it cannot:
a reader with a lexicon containing every drug in the list scores well on both
halves. If you need to measure that, extend the vocabulary lists and hold
words out yourself.

### The no-text corpora

`textureless` and `symbols` carry `"no_text": true` and no `text` key.
Anything a reader emits on them is invented. Treating these as a primary
metric rather than an afterthought is the main opinion this dataset holds:
for a sighted user a spurious word is noise; for a blind user it is an
ingredient that isn't there, with no way to check it.

## Collection and generation

Every image is rendered with PIL from a seeded `random.Random`, then put
through a capture model. Rendering and capture are separate stages on
purpose: holding the scene fixed while changing only the camera is what tells
you whether an algorithm or a lens is the limit.

Modelled: perspective, cylindrical curvature, defocus (disc convolution, not
Gaussian — a defocused lens fills counters and merges strokes in a way
sharpening cannot undo), motion blur, specular glare, sensor noise applied
before compression, exposure drift, MJPEG 4:2:0 chroma subsampling, amber
vial tint, auxiliary warning stickers, symbols crowding text.

Not modelled: creasing and folds, the shadow of the hand holding the object,
reflections carrying an image, fluorescent flicker, rolling shutter, motion
of the subject, any non-Latin script.

The three camera tiers in `generators/webcam.py` resample to the sensor's
real resolution *before* degrading, because a real webcam never had the
pixels a downscaled sharp render would:

| tier | sensor px | defocus | noise | JPEG | models |
|---|---|---|---|---|---|
| `phone` | 1600 | 0.0 | 4.0 | 85 | what the other corpora implicitly assume |
| `webcam` | 1280 | 1.1 | 7.0 | 70 | a decent clip-on, autofocus roughly right |
| `cheap` | 960 | 2.4 | 11.0 | 55 | fixed focus, label inside the near limit |

## Uses

**Intended**: evaluating and comparing text readers for assistive use;
measuring hallucination rate; measuring the cost of camera quality;
regression-testing an OCR pipeline; ablation studies where one variable moves
and the rest are pinned.

**Out of scope**: as a training set on its own. The drug names, pharmacies,
products and statements come from short hand-written lists, so a model
trained on them will learn the vocabulary rather than the task. If you train
on it anyway, hold out by generator seed at minimum, and validate on real
photographs before believing anything.

**Also out of scope**: claiming a reader is safe for medication use. Passing
this corpus is necessary and nowhere near sufficient. Real vials are
scratched, half-peeled, stickered over, and held by someone who cannot see
whether the label is facing the camera.

## Baseline results

Source project, macOS, Apple Vision with RapidOCR as the thorough tier, at
the 2026-09-29 generators. One machine, one engine pairing — a reference
point, not a leaderboard.

```
signage           mean CER 0.019 · exact 97% · silent 0% · invented 0/8
medicine held-out drug name 95% · strength 90% · directions exact 60%
                  dose correct 65% · WRONG dose 0%
labels            product name present 92% (curved 11/12, flat 11/12)
symbols           invented 1/12
allergens         8/40 statement lines recovered · 9/42 allergens reported
                  INVENTED 0 · negations mishandled 0 · hedges 0
fonts             serif 100% · sans 94% · cursive 75% · display 70% · hand 58%
```

Two of those deserve comment.

**The allergen number is the honest failure.** The matcher is correct on
clean text (10/10 in isolation, including negations, hedges, the plant-milk
trap and derived names), so every miss is the reader failing to recover the
line. The statement is rendered at 1.28–2.21% of frame height, and Apple
Vision's minimum text height is a fraction of the *frame*, documented at
around 2%. Three of the four size draws sit at or below that floor. This
looks like an information floor rather than a tuning gap, and it is the
open problem in the corpus.

**Wrong doses stay at 0% while recall triples.** That is the property to
copy. The source project's guard withholds any number on a medical label
unless several frames agree. It costs recall and it took wrong doses from 7%
to 0%. A single accuracy figure would have hidden the trade entirely.

## Known defect, fixed 2026-09-29

Before this version, the cylindrical mapping in `medicine.py` and
`product_labels.py` sampled the source at only ±0.78 of its width
(`asin(k)/(π/2)` was never divided back out). The outer 11–13% of every label
was discarded, and since every field on a pharmacy label starts at the same
left margin, that margin landed within 5 px of the frame edge, squeezed to
60% width. Text detectors need background around a glyph to box it, so the
first character of the drug name was routinely lost — the corpus was partly
scoring a cropping bug rather than a reader.

Renormalizing moved the source project's held-out scores:

| | before | after |
|---|---|---|
| drug name found | 72% | 95% |
| strength found | 78% | 90% |
| full directions exact | 10% | 60% |
| dose read correctly | 22% | 65% |
| WRONG dose | 0% | 0% |
| packaging product name | 79% | 92% |

The packaging gain is entirely on the curved half (8/12 → 11/12) while the
flat half is unchanged at 11/12 — the causal signature you would want, since
only curved samples pass through the mapping at all.

The allergen corpus barely moved (7/40 → 8/40), which is what separates the
two problems: prescriptions were a corpus artifact, allergen statements are a
real reading failure at small text sizes.

**Any prescription or packaging number from before 2026-09-29 is not
comparable with one measured after it.**

## Bias and risk

The vocabulary is small, US-centric and English-only. Pharmacy label
conventions vary by country; a reader tuned on this will be tuned on American
layout. The typefaces are Latin-script Google Fonts.

The failure that matters is asymmetric and a single accuracy number hides it.
Report wrong-answer rate separately from silence. A reader that declines is
unhelpful; a reader that is confidently wrong about an allergen or a dose can
cause harm, and the person it harms is the one who cannot check.

No personal data is present. Patient names on the rendered vials are invented
strings from a fixed list and correspond to no one.

## Maintenance

Corpora are code, so a fix improves every future run rather than leaving a
stale archive behind. The cost is that numbers only compare within one
version of the generators: quote the commit alongside any score.

## Citation

```bibtex
@misc{assistive_ocr_corpus_2026,
  title  = {assistive-ocr-corpus: evaluation corpora for assistive text reading},
  author = {Garnett, Conrad},
  year   = {2026},
  note   = {Extracted from the Med-i-Glasses project, VTHacks}
}
```
