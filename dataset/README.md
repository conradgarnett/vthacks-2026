# Med-i-Glasses assistive-vision dataset

A test set for machine-learning systems that read and describe the world for
blind and low-vision people. It grew out of the Med-i-Glasses project
(`../med-i-glasses/`), where each part was built to catch a specific way such a
system fails its user. It is kept here as a separate folder so that other
projects can use it without the app.

It has two halves, both meant for **evaluation**, not training:

- **Synthetic** (`generate.py`): every image comes from a script with a fixed
  seed, the ground truth is exact, and the hard cases are in it deliberately.
- **Real** (`fetch_real.py`, `real/`): openly licensed photographs of the
  same kinds of things: real ingredient panels and real photos taken by blind
  people. A change that helps on the synthetic half should hold up here.

## The rule behind every metric

The user cannot look to check. A system that stays silent is unhelpful, but
one that confidently says something false is giving them wrong information.
So every task counts **invented** output separately from **missed** output,
and some tasks exist only to measure invention: images with no text at all,
where anything spoken is a failure.

## What is in it

| task | samples × frames | ground truth | what it tests |
|---|---|---|---|
| `signage` | 60 × 3 | the sign's text | wayfinding signs from arm's length to across a room (text 1.2 %–14 % of frame height), off-axis, blurred, glared, JPEG'd |
| `no_text` | 8 textures + 12 symbol images, × 3 | empty | carpet, brick, foliage, blinds, barcodes, recycling marks, nutrition badges, scrollwork, rule grids. **Anything read here is invented.** |
| `medicine` | 40 tuning + 40 **held-out**, × 3 | drug, strength, directions, warning | pharmacy vial labels: curvature, amber plastic, gloss, dense small type, warning stickers. The dose number is scored on its own |
| `packaging` | 24 × 3 | product name | curved cans and bottles in display and script faces, with small print and symbols around the name |
| `allergens` | 40 × 3 | the allergen statement and the allergen groups it really names | small-print CONTAINS / MAY CONTAIN / FREE FROM lines, including negations ("DAIRY FREE"), hedges ("MAY CONTAIN"), product-name traps ("Peanut Butter Cups" over "CONTAINS: MILK, SOY, WHEAT") and plant milks |
| `fonts` | 76 × 3 | the phrase | typeface is the only variable: 38 open-licensed faces in five families (everyday sans, serif, cursive script, handwriting, novelty) |

Each sample has **three frames**: separate simulated hand-held captures of the
same scene. A system can read one frame, or require the frames to agree; the
app requires agreement before it states a dose.

The `medicine` held-out split comes from a separate seed range. Use it only to
report results, never to tune on. If a system scores much better on `tune`
than on `holdout`, it has been fitted to the corpus.

### Real photographs (`real/`)

| task | samples | ground truth | source and licence |
|---|---|---|---|
| `ingredients` | 167 English ingredient panels | the ingredient list (span reviewed by hand), a reference transcription of the whole photo, and the product's allergens | [Open Food Facts ingredient-detection](https://huggingface.co/datasets/openfoodfacts/ingredient-detection) test split. Photos CC BY-SA 3.0 (Open Food Facts contributors), annotations CC BY-SA 4.0, allergens from the Open Food Facts database (ODbL) |
| `no_text` | 196 photos | empty: all five annotators said the photo holds no text (four that turned out to hold some were dropped; see `NOT_TEXT_FREE` in `fetch_real.py`) | [VizWiz](https://vizwiz.org) validation images, taken by blind people with their phones. CC BY 4.0 |

```bash
python dataset/fetch_real.py             # downloads the images for the committed labels
python dataset/fetch_real.py --rebuild   # rebuilds the labels from the sources (slow)
```

The labels are committed and the images are not. Committing the labels fixes
the ground truth, because Open Food Facts is edited all the time and a
product's allergen list can change after it was fetched. Each label records
its image URL and licence. If you redistribute images, credit the source as
its licence requires.

Caveats:

- **The ingredient reference is another engine's reading** (Google Cloud
  Vision), with its own errors ("barlic" for garlic). So reading is scored as
  the share of the ingredient list's words recovered, not as CER against it.
- **The allergens are the product's, not the photo's.** Open Food Facts lists
  allergens from everything known about the product, which can include text
  outside the photographed panel. Traces ("may contain") are a separate list
  and not counted as allergens.
- **Only one frame per sample**, so a system that requires agreement between
  frames sees its single-frame behaviour here.

### Tables (in `tables/`, no images needed)

| file | contents |
|---|---|
| `object_priors.csv` | 140 open-vocabulary detector classes chosen for moving around indoors and outdoors (door, stairs, handrail, curb, power strip…), each with a real-world height prior in metres for pinhole-camera distance estimates, and whether it is an obstacle, a landmark, and hand-held or not. A blank height means no reliable prior: report direction, not a made-up distance |
| `allergen_lexicon.csv` | ingredient words for nine allergen groups, including derived names (whey, casein, albumin, semolina, tahini) |
| `allergen_statements.jsonl` | the 20 statement templates with their true allergen sets, labelled `present`, `hedged` or `absent`. Useful by themselves as a text-only test for negation and hedge handling |
| `product_names.jsonl` | product names that contain an allergen word but are not ingredient lists |
| `signage_phrases.txt`, `prescription_fields.json` | the phrase and field vocabularies the images are drawn from |

## Getting the images

The full set is 300 samples, 900 JPEG frames at about 1600×1200, 98 MB, so it
is not committed. You generate it instead, and it comes out the same every
time:

```bash
pip install numpy pillow
python dataset/generate.py             # everything, into dataset/out/ (~5 min on one CPU core)
python dataset/generate.py --task medicine --task no_text
```

`out/MANIFEST.sha256` lists a hash for every image, so two people can check
they have the same set. Rendering uses only the fonts bundled in
`med-i-glasses/eval/fonts/`, so the pictures do not depend on your operating
system. The JPEG encoder can still differ slightly between Pillow versions.
`sample/` is committed: a few images per task, one frame each, with their
labels, so you can see the data without running anything.

Layout:

```
out/<task>/labels.jsonl        one JSON object per sample
out/<task>/images/<id>_f<k>.jpg
```

```json
{"id": "allergen-000", "frames": ["images/allergen-000_f0.jpg", "..."],
 "task": "allergens", "split": "test",
 "statement": "CONTAINS: MILK, SOY, WHEAT.", "allergens": ["dairy", "gluten", "soy"],
 "hedged": false, "product_name": "Peanut Butter Cups", "curved": true, ...}
```

## Scoring

```bash
python dataset/score.py dataset/out predictions.jsonl
```

`predictions.jsonl` has one line per sample, `{"id": ..., "text": <what your
system would say, "" if silent>}`. For the allergens task you can also add
`"allergens": [...]`, the groups your system would act on. `score.py` uses
only the standard library, and its docstring defines each metric.

- **CER**, not "does the output contain the word". A containment check
  passes "Room 204B" read as "Room 2048", and that check once hid a badly
  performing pipeline.
- **Dose**: the number right after TAKE. A wrong dose is reported apart from
  an unread one: silence is safe, a wrong number is not.
- **Allergens**: missed and invented are reported separately. A missed
  allergen can be eaten. An invented one sends a false alarm, and a few false
  alarms teach the wearer to ignore real ones.

## Reference results

From the project log (`../CLAUDE.md`). Only the `fonts` task matches the
Med-i-Glasses benchmark exactly (`eval/run_font_eval.py`, bundled faces, same
seed). The other tasks swap the benchmark's system fonts for bundled ones, so
compare those numbers with each other, not with the log.

| task | system | result |
|---|---|---|
| `fonts` (n=76) | Med-i-Glasses reader, RapidOCR, CPU | exact match: sans 89 %, serif 100 %, cursive 75 %, handwriting 75 %, novelty 80 % |

**Real photographs**, measured 2026-09-29 with `med-i-glasses/eval/run_dataset_eval.py`
(RapidOCR on a Linux CPU, one frame per sample, reader and allergen matcher at
the commit that added `real/`):

| task | recognizer | result |
|---|---|---|
| `ingredients` (n=167) | RapidOCR's packaged PP-OCRv4 (Chinese) | ingredient words recovered 43.5 %; allergens reported 55 of 189, invented 0 |
| `ingredients` (n=167) | PP-OCRv5 mobile | ingredient words recovered 60.4 %; allergens reported 78 of 189, invented 0 |
| `no_text` (n=196) | either | invented 0 |

Of PP-OCRv5's 111 missed allergens, 69 have their word in the text the reader
recovered: the matcher only counts lines marked as a statement (CONTAINS,
INGREDIENTS…), and a long ingredient list carries that word on its first line
only. Counting the whole read instead reports 135 of 189 but invents 36, so
the fix has to follow the list's own block of text. Results for the other synthetic tasks on
this render are still to be measured. Please add yours here with the system,
engine and hardware named.

## Known limits

- **Synthetic.** The photographic effects are simulated: perspective, blur,
  glare, noise, exposure, curvature, JPEG. Real captures are harder in ways
  this does not model: occlusion by fingers, rolling shutter, very low light.
  A system that does well here still needs testing on real photographs.
- **English, Latin script, US-style pharmacy labels.** The allergen groups
  follow the major US and EU allergen lists but are not complete (mustard,
  celery and lupin are not tracked).
- **Small.** Hundreds of samples, not thousands. It is for telling whether
  something has regressed or invents text, not for fine-grained leaderboards.
  At n=60, one sample is 1.7 points.
- **The patient names and pharmacy names are made up.** Nothing here comes from
  a real person or prescription.
- Height priors are typical adult-world sizes set by hand, not measured
  averages.

## Related data this does not include

- **Receipts**: `med-i-glasses/eval/data/sroie/` has 50 images from the ICDAR
  2019 SROIE dataset, used by the app's receipt benchmark. SROIE belongs to
  its own authors, is under its own terms, and is not part of this dataset.
- **Everyday photographs for the detector**: `med-i-glasses/eval/fetch_everyday.py`
  downloads a COCO val2017 slice (CC BY 4.0, by Flickr owner) for
  `run_detect_eval.py`. It is fetched, not redistributed.

## Licence

See `LICENSE.md`. The fonts are under the SIL Open Font License or the Apache
License 2.0 (`med-i-glasses/eval/fonts/LICENSE-*`).

## Citing

> Med-i-Glasses assistive-vision dataset, v1.0. VTHacks 2026.
> https://github.com/conradgarnett/vthacks-2026/tree/main/dataset
