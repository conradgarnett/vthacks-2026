# assistive-ocr-corpus

Evaluation corpora for **text reading that a blind person will act on** —
prescription vials, food packaging, allergen statements, signage — with the
camera modelled separately from the scene, so you can tell whether your
algorithm or your lens is the limit.

Seven generators, one scorer, 38 open-licensed typefaces, no stored images.
Python 3.10+, `pillow` and `numpy`, nothing else.

```bash
pip install -r requirements.txt
python make_samples.py medicine --holdout --out out/medicine
python make_samples.py textureless --out out/blanks   # contains no text
```

Extracted from [Med-i-Glasses](../README.md), assistive glasses built at
VTHacks. The corpora are the part worth keeping.

---

## Why this exists

Public OCR benchmarks measure documents: flat scans, good light, a page that
fills the frame. None of that is what a cane user meets. They hold a curved
amber vial at 20 cm under a fixed-focus lens, and the field that matters —
the directions — is set smaller than the brand name.

There is also no public prescription-label dataset, and there will not be
one: real labels carry a patient's name, address and prescription number.
Synthesis is not a shortcut here; it is the only lawful way to get a hundred
labels with known ground truth.

Three things this measures that a document OCR benchmark does not:

**Hallucination, as a first-class score.** Two corpora contain no text at
all: 18 real surfaces (brick courses, carpet pile, foliage, venetian blinds,
railings, book spines, radiator fins, corrugated metal, keyboards, pegboard,
tiling, paving, mesh, wood grain, gravel, fabric, shutters, clapboard), and a
symbol corpus of barcodes, recycling marks, nutrition badges and decorative
rules. For a sighted user a spurious word is noise they ignore. For a blind
user it is an ingredient that isn't there, and there is no way to check it.
Report it beside recall or the recall number means nothing.

The surfaces are built to have the structure a detector actually fires on —
high-contrast strokes of roughly consistent height, repeating along a line,
separated by gaps, because that is what a word is. A shelf of book spines is
literally that. **This matters more than it sounds:** an earlier version of
this corpus generated its no-text images from gaussian static with faint
structure added on top, so they had no edges at any scale, nothing ever fired,
and the resulting `0/8` was reported as a safety property while measuring
almost nothing. On the rebuilt surfaces the same reader scores **9/36**.

**Held-out splits, on disjoint seeds.** Thresholds fit whatever you measure
them against. `build_holdout_corpus()` draws from a seed range the tuning set
never touches. In the source project the gap between the two exposed a
genuine overfit — a pipeline tuned until a sign reading `TABLES` was spoken
as `TABLETS`, because the medication vocabulary had leaked into general
reading.

**The camera as a variable.** `generators/webcam.py` has three tiers —
`phone`, `webcam`, `cheap` — that differ in the two ways a clipped-on webcam
fails and a phone does not: it has no focus control, and it resolves far less
than it advertises. Holding the scene fixed and changing only the tier tells
you whether to spend the day on the algorithm or on a better lens. In the
source project that measurement settled an argument: receipts fell 62% → 57%
→ 12% across the three tiers while the drug name barely moved, 100% → 100% →
94%. For small dense print the optics dominate.

## What's in it

| corpus | what it is | ground truth | default n |
|---|---|---|---|
| `signage` | doors, gates, room numbers; 8 apparent sizes from 1.2% to 14% of frame height | the phrase | 60 |
| `medicine` | pharmacy vials: amber tint, cylindrical curvature, 8–12 fields, auxiliary warning stickers | drug, strength, directions, warning | 40 (+40 held out) |
| `labels` | food packaging: curvature, display faces, ingredient small print, specular gloss | product name | 24 |
| `allergens` | allergen statements with negations, hedges, plant-milk traps, derived names | statement + the allergens that should be reported | 40 |
| `fonts` | one benchmark holding every condition fixed and varying only the typeface, over six families | the phrase | 76 |
| `textureless` | 18 surfaces: brick, carpet, foliage, blinds, railings, book spines, radiator, corrugated metal, keyboard, pegboard, tile, paving, mesh, wood, gravel, fabric, shutters, clapboard | **none — any output is invented** | 72 |
| `symbols` | barcodes, recycling marks, nutrition badges, flourishes | **none — any output is invented** | 12 |

Each sample is a burst of frames of one scene. A reader that pools several
frames beats one reading a single frame, and three is what the source project
measured with. Scoring one frame per sample is a different measurement — a
legitimate one; say which you did.

The allergen corpus is deliberately nasty, because the failure is asymmetric:

```
MAY CONTAIN TRACES OF MILK    -> dairy, hedged      (a warning, not a fact)
DAIRY FREE. GLUTEN FREE.      -> nothing            (absence, not presence)
ALMOND MILK. UNSWEETENED.     -> tree nut           (the plant-milk trap)
CONTAINS NO NUTS.             -> nothing
INGREDIENTS: ... WHEY ...     -> dairy              (a derived name)
```

## Using it

As files on disk, for a reader that takes paths, or to train something:

```bash
python make_samples.py signage --n 60 --out out/signage
python make_samples.py allergens --camera cheap --out out/allergens_cheap
```

Each run writes JPEG frames and a `manifest.jsonl`, one record per sample:

```json
{"id": "allergens-0000", "frames": ["0000_0.jpg", "0000_1.jpg", "0000_2.jpg"],
 "statement": "CONTAINS: MILK, SOY, WHEAT.", "allergens": ["dairy", "gluten", "soy"],
 "hedged": false, "product_name": "Peanut Butter Cups", "font": "Brush Script.ttf",
 "scale": 1.3, "curved": true}
```

No-text samples carry `"no_text": true` and **no** `text` key. That is
deliberate: an empty string reads as "the truth is the empty string", while an
absent key says there is nothing to transcribe and anything you emit was
invented.

In Python, if you would rather keep the bytes in memory:

```python
from generators import corpus, medicine, webcam
from metrics import score

samples = medicine.build_holdout_corpus(n=40)
predictions = [my_reader(s["frames"]) for s in samples]
blanks = [my_reader(f) for f in corpus.build_textureless_corpus(n=8)]

print(score(predictions, [s["drug"] for s in samples],
            no_text_predictions=blanks).report("drug name"))
```

Scoring is in `metrics.py`: character error rate via edit distance, exact
match, silence, and hallucination counted only over the no-text corpora.
It is engine-agnostic — it takes strings, and knows nothing about how you
produced them.

**Do not replace CER with "does the output contain the expected word."** That
check passes `Room 204B` read as `Room 2048`, and a reader can look healthy by
it while performing badly in the hand. The source project made this mistake
and measured its way out of it.

## Reproducibility

Every builder takes a seed and is deterministic given one, so the corpora are
generated rather than stored: this repository holds code, not gigabytes of
JPEG, and a fix to a generator improves every future run instead of leaving a
stale archive behind. The trade is that numbers only compare within one
version of the generators — quote the commit.

Use `--fonts bundled` (the default) for numbers that compare across machines.
`--fonts platform` renders with whatever the machine has installed, which
looks more like real signage on that machine but does not compare between a
Mac and a Windows laptop.

**One known break in comparability.** Before 2026-09-29 the cylindrical
mapping in `medicine.py` and `product_labels.py` sampled the source at only
±0.78 of its width, so the outer 11–13% of every label was discarded and each
field's first character landed within 5 px of the frame edge, squeezed to 60%
width. Text detectors need background around a glyph to box it, so the corpus
was partly scoring a cropping bug. Renormalizing the mapping moved the source
project's held-out prescription scores from 72% → 95% (drug name), 10% → 60%
(full directions) and 22% → 65% (dose read correctly), with wrong doses
staying at 0%; packaging product name went 79% → 92%, entirely on the curved
half. If you have prescription or packaging numbers from before that date,
they do not compare.

## Layout

```
generators/       the corpora; PIL + numpy + stdlib only
  corpus.py         signage, and the no-text textures
  medicine.py       pharmacy vials, tuning and held-out splits
  product_labels.py food packaging, and the no-text symbol corpus
  allergens.py      allergen statements
  fonts.py          the typeface benchmark
  typefaces.py      font discovery: bundled, platform, or both
  webcam.py         the three camera tiers
metrics.py        CER, exact match, silence, hallucination
make_samples.py   write a corpus to disk as JPEG + manifest.jsonl
fonts/            38 open-licensed typefaces (see FONT_LICENSES.md)
```

`generators/product_labels.py` is named that way rather than `packaging.py`
on purpose: this directory goes on `sys.path`, and a module named `packaging`
shadows the PyPI package of that name, which nearly every virtualenv has
installed.

## Licence

Code and generated images: **MIT** (`LICENSE`). The bundled typefaces keep
their own licences — SIL OFL 1.1 and Apache 2.0, per face, in
`FONT_LICENSES.md`. Third-party datasets the source project evaluated
against are **not** redistributed here; `THIRD_PARTY.md` says where to get
them and under what terms.

Nothing in this repository is derived from a real prescription, a real
patient, or any personal data. Every label is invented.

## Limits — read before you cite a number

**These are renders, not photographs.** They model perspective, motion blur,
defocus, glare, sensor noise, exposure drift and JPEG artifacts, and they do
not model everything: no creasing, no folds, no shadow of the hand holding
the bottle, no reflections that carry an image, no fluorescent flicker, no
rolling shutter. A reader that does well here can still do badly on a photo.
Validate on real images before you believe a number — the source project used
[SROIE](THIRD_PARTY.md) receipts for exactly that, and its synthetic and real
scores differed.

**The vocabulary is small.** The drug names, pharmacies, products and
statements are short hand-written lists, so a model *trained* on this will
learn them. It is built as an evaluation set. If you train on it, hold out by
generator seed at minimum, and better, validate on real photographs.

**English only**, Latin script, US-style pharmacy label conventions.

**The hazard the numbers hide.** In assistive use the two error types are not
symmetric, and a single accuracy figure hides the one that hurts. Report
wrong-answer rate separately from silence, always. The source project's dose
guard withholds any number on a medical label unless several frames agree; it
costs recall and it took wrong doses from 7% to 0%.

## Credit

Built for [Med-i-Glasses](https://github.com/) at VTHacks by Conrad Garnett,
with Charlie Savage, and Claude. If it is useful in your work, a link back is
plenty.
