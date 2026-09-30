# Third-party data

**Nothing in this section is redistributed here.** The source project
evaluated against these; this file says where to get them and under what
terms, so you can reproduce that work without inheriting a licence problem
from us.

Everything this repository actually ships — the generators, the scorer, the
images they produce — is MIT (`LICENSE`), except the typefaces, which keep
their own (`FONT_LICENSES.md`).

## SROIE (ICDAR 2019, Task 1)

Scanned receipts with per-line ground truth. **The most valuable single
addition to this corpus**, because it is the closest public analogue to a
pharmacy label: dense small print, creased paper, handheld capture, real
optics. Synthetic renders will flatter a reader; these will not.

- Source: ICDAR 2019 Robust Reading Competition, Task 1 —
  <https://rrc.cvc.uab.es/?ch=13>
- Terms: registration required; academic/research use. Check the current
  terms yourself before redistributing anything derived from it. We do not
  redistribute it and neither should you without reading them.
- Size: the source project used a 50-image sample, about 17 MB.

Put it at `data/sroie/` with the images and their ground-truth text files
side by side. That path is gitignored in the source project on purpose.

The source project's number on that 50-image sample: **54% of ground-truth
lines recovered exactly** (median 55% per receipt, worst 28%, best 86%),
using Apple Vision then RapidOCR. Quote it as a reference point, not a
benchmark result — 50 images is a sample, not the dataset.

## COCO (val2017)

Everyday photographs, used in the source project to measure the object
detector, not the reader. Not needed for anything in this repository.

- Source: <https://cocodataset.org>
- Images: mostly Flickr, various Creative Commons terms per image.
- Annotations: CC BY 4.0.

## The dataset that does not exist

There is no public dataset of real prescription labels, and there will not
be one. A real label carries the patient's name, address, prescriber and
prescription number — it is health information about an identifiable person
in every jurisdiction that has such a category.

This is why `generators/medicine.py` exists. It is not a convenience or a
stand-in for data someone forgot to collect: synthesis is the only lawful way
to get a hundred pharmacy labels with known ground truth. The trade is that
it models US-style label conventions from a short hand-written vocabulary,
so it measures a reader and should not be used to *train* one without real
validation data. See "Limits" in the README.

**If you photograph real labels for your own validation set:** they are yours
to hold and not yours to publish. Do not commit them, do not put them in an
issue, do not paste one into a model API you have not checked the retention
terms for. The patient did not consent to any of that.

## Other datasets worth knowing about

Not used by the source project; listed because they are the obvious next
things to reach for, with the terms that decide whether you can.

| Dataset | What | Terms |
|---|---|---|
| [ICDAR 2015 Incidental Scene Text](https://rrc.cvc.uab.es/?ch=4) | scene text, handheld, blurry | registration; research use |
| [Total-Text](https://github.com/cs-chan/Total-Text-Dataset) | curved and oriented text — closest public match to a cylindrical label | BSD-3 |
| [CTW1500](https://github.com/Yuliang-Liu/Curve-Text-Detector) | curved text lines | research use |
| [TextOCR](https://textvqa.org/textocr/) | 1M annotated words on everyday images | CC BY 4.0 |
| [The Drug Name Detection Dataset](https://www.kaggle.com/datasets/pkdarabi/the-drug-name-detection-dataset) | drug names on packaging — boxes, not vials, and no patient data | check the Kaggle listing |

Total-Text and CTW1500 are the two most relevant: curvature is the property
that separates a vial or a can from a page, and it is exactly what
`generators/medicine.py` and `generators/product_labels.py` simulate.
Validating the simulation against real curved text is the honest next step.
