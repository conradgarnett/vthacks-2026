"""Write the Med-i-Glasses assistive-vision dataset to disk.

    python dataset/generate.py                 # full set into dataset/out/
    python dataset/generate.py --sample        # a few images per task into dataset/sample/
    python dataset/generate.py --tables-only   # just the tables in dataset/tables/

Needs numpy and pillow and nothing else. The images are not stored in the
repository: they come from the same generators the app's own benchmarks use
(`med-i-glasses/eval/`), with the same seeds, so there is exactly one
definition of each corpus and a number measured here compares with the
numbers in the project log.

One difference from the app's benchmarks, on purpose: every image here is
set in the open-licensed faces committed under `med-i-glasses/eval/fonts/`.
The app's benchmarks use a machine's system fonts by default, so a Mac and a
Windows laptop render different pictures. A dataset should be the same
pictures for everyone, so the system faces are swapped for bundled ones (a
condensed sans for the label small print, a plain sans for pharmacy labels).
Numbers from this dataset therefore compare with each other, and with the
app's `--fonts bundled` runs, but not with its platform-font runs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent / "med-i-glasses"
EVAL = PROJECT / "eval"
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(PROJECT))

import allergens
import corpus
import fonts
import medicine
import packaging
from typefaces import FONT_DIR, bundled_fonts

VERSION = "1.0"

# Faces standing in for the system fonts the eval generators ask for.
_SANS = str(FONT_DIR / "OpenSans-Variable.ttf")
_CONDENSED = str(FONT_DIR / "Oswald-Variable.ttf")
_SIGNAGE_FAMILIES = ("everyday_sans", "everyday_serif")


def _pin_fonts() -> list[str]:
    """Make every generator render with bundled faces only.

    Returns the signage font pool. Signage uses the everyday sans and serif
    faces: that is what wayfinding signs are set in, and the script faces
    already have their own task.
    """
    from PIL import ImageFont

    def load(path: str, px: int, fallback: str):
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            return ImageFont.truetype(fallback, px)

    # Pharmacy labels ask for Arial Narrow and Arial Bold.
    medicine._font = lambda path, px: load(_SANS, px, _SANS)

    # Packaging and allergen labels: display faces for the product name,
    # Arial Narrow for the small print. Off a Mac the eval code falls back to
    # the first bundled face, which is a brush script; a condensed sans is
    # what that print really looks like.
    display = bundled_fonts()
    packaging.DISPLAY_FONTS = display
    allergens.DISPLAY_FONTS = display

    def label_font(path: str, px: int):
        return load(_CONDENSED if not path.startswith(str(FONT_DIR)) else path, px, _SANS)

    packaging._font = label_font
    allergens._font = label_font

    families = fonts.bundled_families()
    return [p for fam in _SIGNAGE_FAMILIES for p in families.get(fam, [])]


# -- tasks -----------------------------------------------------------------
#
# Each task yields (id, frames, record). A record is the label line minus the
# frame paths, which the writer fills in. Sizes and seeds are the ones the
# project log quotes, so "n=60 signage" here is the same sixty signs.


def task_signage(n, signage_fonts):
    for i, s in enumerate(corpus.build_corpus(n=n, seed=11, fonts=signage_fonts)):
        yield f"signage-{i:03d}", s.frames, {
            "task": "signage", "split": "test", "text": s.truth,
            "font": s.font, "text_height_frac": s.height_pct,
        }


def task_no_text(n):
    kinds = ["carpet", "brick", "foliage", "blinds"]
    for i, frames in enumerate(corpus.build_textureless_corpus(n=n, seed=23)):
        yield f"notext-texture-{i:03d}", frames, {
            "task": "no_text", "split": "test", "text": "", "kind": kinds[i % 4],
        }
    for i, s in enumerate(packaging.build_symbol_corpus(n=max(1, n + n // 2), seed=37)):
        yield f"notext-symbol-{i:03d}", s["frames"], {
            "task": "no_text", "split": "test", "text": "", "kind": s["kind"],
        }


def task_medicine(n):
    for split, samples in (
        ("tune", medicine.build_medicine_corpus(n=n, seed=101)),
        ("holdout", medicine.build_holdout_corpus(n=n)),
    ):
        for i, s in enumerate(samples):
            yield f"medicine-{split}-{i:03d}", s["frames"], {
                "task": "medicine", "split": split,
                "drug": s["drug"], "strength": s["strength"],
                "directions": s["directions"], "warning": s["warning"],
                "amber_vial": s["amber"], "type_scale": s["scale"],
            }


def task_packaging(n):
    for i, s in enumerate(packaging.build_packaging_corpus(n=n, seed=31)):
        yield f"packaging-{i:03d}", s["frames"], {
            "task": "packaging", "split": "test", "text": s["truth"],
            "font": s["font"], "curved": s["curved"], "type_scale": s["scale"],
        }


def task_allergens(n):
    for i, s in enumerate(allergens.build_allergen_corpus(n=n, seed=41)):
        yield f"allergen-{i:03d}", s["frames"], {
            "task": "allergens", "split": "test",
            "statement": s["statement"], "allergens": sorted(s["truth"]),
            "hedged": s["hedged"], "product_name": s["name"],
            "font": s["font"], "curved": s["curved"], "type_scale": s["scale"],
        }


def task_fonts(phrases_per_font, per_family=None):
    samples = fonts.build_font_corpus(seed=41, phrases_per_font=phrases_per_font, bundled=True)
    seen: dict[str, int] = {}
    for i, s in enumerate(samples):
        seen[s["family"]] = seen.get(s["family"], 0) + 1
        if per_family and seen[s["family"]] > per_family:
            continue
        yield f"font-{i:03d}", s["frames"], {
            "task": "fonts", "split": "test", "text": s["truth"],
            "font": s["font"], "font_family": s["family"],
        }


FULL = {"signage": 60, "no_text": 8, "medicine": 40, "packaging": 24, "allergens": 40, "fonts": 2}
SAMPLE = {"signage": 3, "no_text": 2, "medicine": 2, "packaging": 2, "allergens": 3, "fonts": 1,
          "fonts_per_family": 1}


def _tasks(sizes, signage_fonts):
    return {
        "signage": lambda: task_signage(sizes["signage"], signage_fonts),
        "no_text": lambda: task_no_text(sizes["no_text"]),
        "medicine": lambda: task_medicine(sizes["medicine"]),
        "packaging": lambda: task_packaging(sizes["packaging"]),
        "allergens": lambda: task_allergens(sizes["allergens"]),
        "fonts": lambda: task_fonts(sizes["fonts"], sizes.get("fonts_per_family")),
    }


def write_images(out: Path, sizes: dict, only: list[str] | None, frames_kept: int | None) -> None:
    signage_fonts = _pin_fonts()
    tasks = _tasks(sizes, signage_fonts)
    manifest: dict[str, str] = {}
    for name, build in tasks.items():
        if only and name not in only:
            continue
        task_dir = out / name
        if task_dir.exists():
            shutil.rmtree(task_dir)
        (task_dir / "images").mkdir(parents=True)
        count = 0
        with open(task_dir / "labels.jsonl", "w", encoding="utf-8") as labels:
            for sample_id, frames, record in build():
                paths = []
                for k, jpeg in enumerate(frames[:frames_kept]):
                    rel = f"images/{sample_id}_f{k}.jpg"
                    (task_dir / rel).write_bytes(jpeg)
                    manifest[f"{name}/{rel}"] = hashlib.sha256(jpeg).hexdigest()
                    paths.append(rel)
                labels.write(json.dumps({"id": sample_id, "frames": paths, **record}) + "\n")
                count += 1
        print(f"  {name:<10} {count:>4} samples")
    (out / "MANIFEST.sha256").write_text(
        "".join(f"{digest}  {path}\n" for path, digest in sorted(manifest.items())),
        encoding="utf-8",
    )
    (out / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")


# -- tables ----------------------------------------------------------------


def write_tables(out: Path) -> None:
    """Small text tables, useful on their own and committed to the repo."""
    from backend.alerts.allergy import ALLERGEN_WORDS
    from backend.perception.vocabulary import VOCABULARY, scale_of

    out.mkdir(parents=True, exist_ok=True)

    with open(out / "object_priors.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["class", "height_m", "is_obstacle", "is_landmark", "scale"])
        for label, spec in VOCABULARY.items():
            w.writerow([
                label, "" if spec.height_m is None else spec.height_m,
                int(spec.is_obstacle), int(spec.is_landmark), scale_of(label),
            ])

    with open(out / "allergen_lexicon.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["allergen_group", "ingredient_word"])
        for group, words in ALLERGEN_WORDS.items():
            for word in words:
                w.writerow([group, word])

    with open(out / "allergen_statements.jsonl", "w", encoding="utf-8") as f:
        for lines, truth, hedged in allergens.STATEMENTS:
            if not truth:
                case = "absent"
            elif hedged:
                case = "hedged"
            else:
                case = "present"
            f.write(json.dumps({
                "statement": " ".join(lines), "allergens": sorted(truth),
                "hedged": hedged, "case": case,
            }) + "\n")

    with open(out / "product_names.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps({"name": name, "subtitle": sub, "note":
                "product name, not an ingredient list"}) + "\n" for name, sub in allergens.NAMES)

    (out / "signage_phrases.txt").write_text("\n".join(corpus.PHRASES) + "\n", encoding="utf-8")

    (out / "prescription_fields.json").write_text(json.dumps({
        "drugs": [{"drug": d, "strength": s} for d, s in medicine.DRUGS],
        "directions": medicine.DIRECTIONS,
        "warnings": medicine.WARNINGS,
        "pharmacies": medicine.PHARMACIES,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"  tables     written to {out.relative_to(HERE.parent)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write images (default dataset/out, or dataset/sample)")
    parser.add_argument("--sample", action="store_true",
                        help="a few samples per task, one frame each (what is committed)")
    parser.add_argument("--tables-only", action="store_true")
    parser.add_argument("--task", action="append", choices=list(FULL),
                        help="only these tasks (repeatable)")
    args = parser.parse_args()

    write_tables(HERE / "tables")
    if args.tables_only:
        return 0
    out = args.out or (HERE / ("sample" if args.sample else "out"))
    sizes = SAMPLE if args.sample else FULL
    print(f"  rendering into {out}  (a full run takes a few minutes)")
    write_images(out, sizes, args.task, 1 if args.sample else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
