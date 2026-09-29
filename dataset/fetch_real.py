"""Real photographs to set beside the synthetic corpora, from open sources.

    python dataset/fetch_real.py                 # images for the committed labels
    python dataset/fetch_real.py --rebuild       # rebuild the labels from the sources

The synthetic tasks say whether a change helped under controlled conditions.
These say whether it holds on real photographs. Two sources, both openly
licensed, both about what a blind user actually points a camera at:

  ingredients  Open Food Facts' ingredient-detection test split: real phone
               photos of ingredient panels, contributed by the public. Each
               has a reference transcription of the whole photo (Google Cloud
               Vision), the ingredient list inside it marked and reviewed by
               hand, and the product's barcode, which gives Open Food Facts'
               own allergen list for the product. English panels only.

  no_text      VizWiz: photographs taken by blind people, each captioned by
               five sighted annotators who also marked whether the picture
               holds any text. Only photos all five said have none are kept,
               so anything a reader says about them is invented.

The label files (`real/<task>/labels.jsonl`) are small and committed, which
pins the ground truth: Open Food Facts is edited continuously, and a product's
allergens can change after this was fetched. Images are downloaded, not
committed; `real/` beside the labels is gitignored.

Standard library only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "real"
USER_AGENT = "Med-i-Glasses-dataset/1.0 (+https://github.com/conradgarnett/vthacks-2026)"

OFF_DATASET = (
    "https://huggingface.co/datasets/openfoodfacts/ingredient-detection/resolve/"
    "9f5f92ad195be565cfc582541f6a426079241b50/ingredient_detection_dataset_test.jsonl.gz"
)
OFF_PRODUCT = "https://world.openfoodfacts.org/api/v2/product/{code}.json?fields=lang,allergens_tags,traces_tags,product_name"
OFF_IMAGE = "https://images.openfoodfacts.org/images/products/{path}/{image_id}.jpg"
OFF_LICENSE = (
    "Photo: Open Food Facts contributors, CC BY-SA 3.0. Transcription and "
    "ingredient span: openfoodfacts/ingredient-detection, CC BY-SA 4.0. "
    "Allergens: Open Food Facts database, ODbL."
)

VIZWIZ_ANNOTATIONS = "https://vizwiz.cs.colorado.edu/VizWiz_final/caption/annotations.zip"
VIZWIZ_LICENSE = "VizWiz (Gurari et al.), CC BY 4.0"

# Open Food Facts allergen tags -> the groups the allergy scanner tracks.
# Celery, mustard, lupin and sulphites have no group there and are left out.
OFF_ALLERGENS = {
    "en:milk": "dairy",
    "en:gluten": "gluten",
    "en:nuts": "tree nut",
    "en:peanuts": "peanut",
    "en:eggs": "egg",
    "en:soybeans": "soy",
    "en:sesame-seeds": "sesame",
    "en:fish": "fish",
    "en:crustaceans": "shellfish",
    "en:molluscs": "shellfish",
}

# Reviewed against the panel's own reference transcription, 2026-09-29. The
# product's Open Food Facts allergen list missed an allergen the panel states
# outright (a CONTAINS line, or an ingredient). Only presences are added;
# "may contain", trace and factory warnings never are. The unreviewed list is
# kept in each label as `off_allergens`.
REVIEWED: dict[str, tuple[list[str], str]] = {
    "off-0018000336869-4": (["dairy"], "Whey; CONTAINS WHEAT AND MILK INGREDIENTS"),
    "off-0034000432271-3": (["peanut", "soy"], "CONTAINS: PEANUTS, MILK, SOY"),
    "off-0049022127548-2": (["tree nut"], "PISTACHIOS; CONTAINS: PISTACHIOS"),
    "off-0071873281018-1": (["dairy", "soy"], "milkfat (read 'milkiat'), soy lecithin"),
    "off-0080364004029-1": (["dairy"], "PASTEURIZED PART-SKIM MILK; CONTAINS: MILK"),
    "off-0705599014697-2": (["dairy", "gluten"], "Contains milk and wheat (egg, soy, tree nuts are trace amounts only)"),
    "off-0813535003312-3": (["dairy"], "whey protein isolate"),
    "off-4088700057292-3": (["gluten", "soy"], "CONTAINS WHEAT AND SOY (crustacea, egg, fish, milk, sesame: may be present only)"),
    "off-4802222039969-2": (["peanut"], "Roasted Imported Peanuts; Contains Peanuts"),
    "off-5010052111420-1": (["dairy", "gluten"], "Wheat Flour, Rye Flour, Lactose (Milk), Whey Powder (Milk)"),
    "off-5019180091406-1": (["dairy", "egg", "gluten", "soy"], "Milk, Pasteurised Egg, Wheat Flour, Soya Lecithins (peanuts and nuts: may contain only)"),
    "off-5036589253150-1": (["dairy"], "Organic whole MILK yogurt"),
    "off-5052319332520-1": (["dairy", "egg", "gluten", "soy"], "Wheat Flour, Pasteurised Egg, Whey Powder (Milk), Soya Lecithins"),
    "off-8031119001253-1": (["dairy", "gluten", "sesame", "shellfish", "soy"], "CONTAINS WHEAT, SOY, MILK, AND SHRIMP INGREDIENTS; SESAME OIL"),
    "off-9351777001297-3": (["soy"], "Allergens: Contains Soy (Lecithin)"),
    "off-5060560280033-3": ([], "reviewed: milk, gluten, egg, soya and other nuts are a factory warning, not ingredients"),
}

# VizWiz photos all five annotators called text-free that hold text after all,
# found when a reader "invented" on them (2026-09-29). Kept out of no_text.
NOT_TEXT_FREE: dict[str, str] = {
    "VizWiz_val_00000076.jpg": "a printed clothing tag, bottom right",
    "VizWiz_val_00001012.jpg": "a video magnifier's MODE and BRIGHTNESS buttons",
    "VizWiz_val_00001466.jpg": "a dimmed phone lock screen reading 10:42",
    "VizWiz_val_00006341.jpg": "DVD spines and a captioned TV picture",
}


def _get(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (404, 410):
                raise
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 ** attempt)
    return _get_once(request, timeout)


def _get_once(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _barcode_path(code: str) -> str:
    """Open Food Facts stores a 13-digit barcode as 3/3/3/4 folders."""
    if len(code) <= 8:
        return code
    code = code.zfill(13)
    return f"{code[:3]}/{code[3:6]}/{code[6:9]}/{code[9:]}"


# -- rebuilding the labels ---------------------------------------------------


def rebuild_ingredients(limit: int | None) -> list[dict]:
    rows = [json.loads(line) for line in gzip.decompress(_get(OFF_DATASET)).splitlines()]
    labels = []
    for row in rows:
        meta = row["meta"]
        code = meta["barcode"]
        spans = [row["text"][start:end] for start, end in row["offsets"]]
        if not spans:
            continue  # a photo with no ingredient list in it
        try:
            product = json.loads(_get(OFF_PRODUCT.format(code=code))).get("product") or {}
        except urllib.error.HTTPError:
            continue
        time.sleep(0.7)  # Open Food Facts asks for at most 100 product reads a minute
        if product.get("lang") != "en":
            continue
        tags = product.get("allergens_tags") or []
        sample_id = f"off-{code}-{meta['image_id']}"
        off_allergens = sorted({OFF_ALLERGENS[t] for t in tags if t in OFF_ALLERGENS})
        added, review = REVIEWED.get(sample_id, ([], ""))
        labels.append({
            "id": sample_id,
            "frames": [f"images/{sample_id}.jpg"],
            "task": "ingredients",
            "split": "test",
            "text": row["text"],
            "ingredients": spans,
            "allergens": sorted(set(off_allergens) | set(added)),
            **({"allergens_review": review} if review else {}),
            "off_allergens": off_allergens,
            "off_allergens_tags": tags,
            "off_traces_tags": product.get("traces_tags") or [],
            "product_name": product.get("product_name", ""),
            "url": OFF_IMAGE.format(path=_barcode_path(code), image_id=meta["image_id"]),
            "license": OFF_LICENSE,
        })
        print(f"\r  ingredients  {len(labels)} English panels", end="", flush=True)
        if limit and len(labels) >= limit:
            break
    print()
    return labels


def rebuild_no_text(limit: int, seed: int = 7) -> list[dict]:
    import io
    import zipfile

    archive = zipfile.ZipFile(io.BytesIO(_get(VIZWIZ_ANNOTATIONS)))
    val = json.loads(archive.read("annotations/val.json"))
    saw_text: dict[int, list[bool]] = {}
    for annotation in val["annotations"]:
        saw_text.setdefault(annotation["image_id"], []).append(bool(annotation["text_detected"]))
    # Every annotator said no text, and there were five of them.
    images = [
        image for image in val["images"]
        if len(saw_text.get(image["id"], [])) >= 5 and not any(saw_text[image["id"]])
    ]
    random.Random(seed).shuffle(images)
    labels = []
    # The excluded photos are dropped, not replaced, so a rebuild reproduces
    # the committed labels exactly.
    chosen = [i for i in images[:limit] if i["file_name"] not in NOT_TEXT_FREE]
    for image in sorted(chosen, key=lambda i: i["file_name"]):
        stem = Path(image["file_name"]).stem
        labels.append({
            "id": f"vizwiz-{stem}",
            "frames": [f"images/{image['file_name']}"],
            "task": "no_text",
            "split": "test",
            "text": "",
            "kind": "vizwiz",
            "url": f"https://vizwiz.cs.colorado.edu/VizWiz_visualization_img/{image['file_name']}",
            "license": VIZWIZ_LICENSE,
        })
    print(f"  no_text      {len(labels)} VizWiz photos ({len(images)} qualified)")
    return labels


def _write_labels(task: str, labels: list[dict]) -> None:
    folder = OUT / task
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "labels.jsonl", "w", encoding="utf-8") as out:
        out.writelines(json.dumps(label, ensure_ascii=False) + "\n" for label in labels)


# -- fetching the images -----------------------------------------------------


def fetch_images() -> int:
    missing = 0
    for labels_file in sorted(OUT.glob("*/labels.jsonl")):
        folder = labels_file.parent
        rows = [json.loads(line) for line in labels_file.read_text(encoding="utf-8").splitlines() if line]
        got = 0
        for row in rows:
            target = folder / row["frames"][0]
            if not target.exists():
                try:
                    data = _get(row["url"])
                except Exception as error:  # noqa: BLE001 - report and carry on
                    print(f"\n  could not fetch {row['id']}: {error}")
                    missing += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            got += 1
            print(f"\r  {folder.name:<12} {got}/{len(rows)} images", end="", flush=True)
        print()
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the committed labels from the sources (slow: rate-limited API)")
    parser.add_argument("--ingredients", type=int, default=0, help="cap on panels when rebuilding (0 = all)")
    parser.add_argument("--no-text", type=int, default=200, help="VizWiz photos when rebuilding")
    args = parser.parse_args()

    if args.rebuild:
        _write_labels("ingredients", rebuild_ingredients(args.ingredients or None))
        _write_labels("no_text", rebuild_no_text(args.no_text))
    if not any(OUT.glob("*/labels.jsonl")):
        print("No labels yet; run with --rebuild.")
        return 1
    missing = fetch_images()
    if missing:
        print(f"{missing} images could not be fetched; score.py skips samples without predictions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
