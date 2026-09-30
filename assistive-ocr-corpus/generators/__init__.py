"""Corpus generators for assistive text reading.

Each module renders a scene, then puts it through a camera model. The
rendering and the camera are separate on purpose: a score only means
something next to the capture conditions that produced it, and holding the
scene fixed while changing the camera is how you find out whether the
algorithm or the lens is the limit.

    from generators import corpus, medicine, product_labels, allergens, fonts, webcam

    samples = corpus.build_corpus(n=60, seed=11)          # signage
    blanks  = corpus.build_textureless_corpus(n=8)        # no text at all
    labels  = medicine.build_medicine_corpus(n=40)        # pharmacy vials
    heldout = medicine.build_holdout_corpus(n=40)         # disjoint seeds

Every builder takes a seed and is deterministic given one. The held-out
medicine set draws from a seed range the tuning set never touches, so a score
on it says something about unseen labels; a score on the tuning set does not.

The modules import each other flatly (`from typefaces import ...`) so they
also run as plain scripts from inside this folder. Importing the package puts
this directory on `sys.path` to make both spellings work.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

__all__ = [
    "corpus",
    "medicine",
    "product_labels",
    "allergens",
    "fonts",
    "typefaces",
    "webcam",
]
