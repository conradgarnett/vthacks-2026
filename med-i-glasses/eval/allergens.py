"""Allergen statements on packaging, rendered and then photographed.

The allergy scanner emails a doctor off one of these lines, so the corpus
exists to measure the two ways that goes wrong, which are not symmetric:

  a MISSED allergen is a person eating something they react to;
  an INVENTED allergen is an email about a peanut that was a chickpea,
  and a few of those teach the wearer to ignore the alert.

Invented wants zero. Missed wants low.

The statements deliberately include the cases that break naive matching:

  negations     "DAIRY FREE", "NO NUTS", "DOES NOT CONTAIN MILK" -- these
                name an allergen and mean its absence. A substring match
                emails on every one of them.
  hedges        "MAY CONTAIN", "TRACES OF", "MADE IN A FACILITY" -- real,
                but not the same claim as CONTAINS, and the ladder speaks
                them rather than emailing.
  name traps    "PEANUT BUTTER CUPS" as a product name. A name is not an
                ingredient list.
  plant milks   "ALMOND MILK", "OAT MILK", "COCONUT YOGURT" -- the word
                milk without the dairy.
  derived names "WHEY", "CASEIN", "SEMOLINA", "ALBUMIN" -- the allergen
                under a name the wearer's profile does not use.

Small print is the whole difficulty: these lines are set at a fraction of
the label and then curved, blurred, glared and JPEG'd like everything else
in packaging.py.
"""

from __future__ import annotations

import os
import random

import numpy as np
from PIL import Image, ImageDraw

from packaging import (
    DISPLAY_FONTS,
    H,
    W,
    _font,
    capture_package,
    draw_barcode,
    draw_recycling,
)

# (statement lines, allergens that should be reported, hedged?)
# An empty set means: reporting anything here is an invention.
STATEMENTS: list[tuple[list[str], set[str], bool]] = [
    # --- plain CONTAINS: the case that is allowed to email ---------------
    (["CONTAINS: MILK, SOY, WHEAT."], {"dairy", "soy", "gluten"}, False),
    (["ALLERGENS: EGG, FISH."], {"egg", "fish"}, False),
    (["CONTAINS PEANUTS."], {"peanut"}, False),
    (["CONTAINS: TREE NUTS (ALMOND, CASHEW),", "SOY."], {"tree nut", "soy"}, False),
    (["INGREDIENTS: WHEAT FLOUR, SUGAR, WHEY,", "SOY LECITHIN, SALT."],
     {"gluten", "dairy", "soy"}, False),
    (["INGREDIENTS: DURUM SEMOLINA, WATER,", "EGG ALBUMIN."], {"gluten", "egg"}, False),
    (["CONTAINS: SHELLFISH (SHRIMP, CRAB)."], {"shellfish"}, False),
    # mustard is not a group the matcher tracks: only sesame is reportable here.
    (["CONTAINS: SESAME, MUSTARD."], {"sesame"}, False),
    # --- hedged: real, but speaks rather than emails ---------------------
    (["MAY CONTAIN PEANUTS AND TREE NUTS."], {"peanut", "tree nut"}, True),
    (["MAY CONTAIN TRACES OF MILK."], {"dairy"}, True),
    (["MADE IN A FACILITY THAT PROCESSES", "PEANUTS AND SOY."], {"peanut", "soy"}, True),
    # --- negations: naming an allergen to deny it ------------------------
    (["DAIRY FREE. GLUTEN FREE."], set(), False),
    (["CONTAINS NO NUTS."], set(), False),
    (["DOES NOT CONTAIN MILK OR EGG."], set(), False),
    (["FREE FROM: PEANUTS, TREE NUTS, SOY."], set(), False),
    # --- name traps and plant milks --------------------------------------
    (["ALMOND MILK. UNSWEETENED."], {"tree nut"}, False),
    (["OAT MILK BARISTA BLEND."], set(), False),
    (["COCONUT YOGURT ALTERNATIVE."], set(), False),
    # --- nothing to say at all --------------------------------------------
    (["INGREDIENTS: CARBONATED WATER, CITRIC ACID,", "NATURAL FLAVOR, CAFFEINE."],
     set(), False),
    (["INGREDIENTS: TOMATOES, SALT, BASIL."], set(), False),
]

# Product names, which sit large on the label. Two are traps: the allergen
# word is in the NAME, and a name is not an ingredient list.
NAMES = [
    ("Peanut Butter Cups", "Milk Chocolate"),
    ("Almond Crunch Bar", "Dark Chocolate"),
    ("Sandwich Crackers", "Cheese"),
    ("Breakfast Cereal", "Honey Nut"),
    ("Pasta Sauce", "Basil"),
    ("Rice Crackers", "Sea Salt"),
]


def render_allergen_label(
    name: tuple[str, str], statement: list[str], font_path: str, scale: float, rng
) -> Image.Image:
    """A label whose small print is the statement under test."""
    base = rng.choice(["#b91c1c", "#1d4ed8", "#065f46", "#f4f4f5", "#18181b", "#ea580c"])
    ink = "#f8fafc" if base != "#f4f4f5" else "#18181b"

    img = Image.new("RGB", (W, H), "#6b7280")
    d = ImageDraw.Draw(img)
    panel = [W // 2 - 520, H // 2 - 400, W // 2 + 520, H // 2 + 400]
    d.rectangle(panel, fill=base)
    d.rectangle([panel[0], H // 2 - 250, panel[2], H // 2 - 60], fill="#00000022")

    d.text((panel[0] + 60, H // 2 - 240), name[0],
           fill=ink, font=_font(font_path, max(26, int(H * 0.085 * scale))))
    d.text((panel[0] + 60, H // 2 - 40), name[1],
           fill=ink, font=_font(font_path, max(18, int(H * 0.042 * scale))))

    # The statement, in the size these are actually printed at.
    tiny = _font("/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
                 max(13, int(H * 0.017 * scale)))
    for i, line in enumerate(statement):
        d.text((panel[0] + 60, H // 2 + 90 + i * int(H * 0.026 * scale)),
               line, fill=ink, font=tiny)

    # Symbols crowd the small print on a real package.
    draw_barcode(d, panel[2] - 330, H // 2 + 250, 270, 110, rng)
    draw_recycling(d, panel[0] + 130, H // 2 + 300, 58)
    return img


def build_allergen_corpus(n: int = 40, seed: int = 41, frames: int = 3):
    """Labels carrying an allergen statement, photographed hand-held."""
    rng = random.Random(seed)
    np.random.seed(seed)
    samples = []
    for i in range(n):
        statement, truth, hedged = STATEMENTS[i % len(STATEMENTS)]
        name = NAMES[i % len(NAMES)]
        font = DISPLAY_FONTS[i % len(DISPLAY_FONTS)]
        scale = rng.choice([0.75, 1.0, 1.0, 1.3])
        curved = i % 2 == 0
        base = render_allergen_label(name, statement, font, scale, rng)
        samples.append({
            "frames": [capture_package(base, rng, curved) for _ in range(frames)],
            "statement": " ".join(statement),
            "truth": truth,
            "hedged": hedged,
            "name": name[0],
            "font": os.path.basename(font),
            "scale": scale,
            "curved": curved,
        })
    return samples
