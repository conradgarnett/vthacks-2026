"""Fetch a labeled set of everyday objects: a slice of COCO val2017.

    cd med-i-glasses && PYTHONPATH=. .venv/bin/python eval/fetch_everyday.py [--count 300] [--seed 7]

COCO's 80 classes are the everyday things (cups, bottles, forks, laptops,
phones, remotes, chairs, couches, beds, toilets, ovens, clocks, scissors,
toothbrushes ...) and its validation set is labeled exhaustively for them,
so a detector's misses and inventions can both be counted. Two downloads:

  coco2017labels.zip (46 MB) from github.com/ultralytics/assets: YOLO-format
  boxes for train and val; only the val2017 files are kept.
  N photographs (default 300, about 160 KB each) from
  images.cocodataset.org, chosen with a fixed seed among val images that
  contain at least one indoor everyday class, so the slice leans to rooms
  rather than to streets and zebras.

Everything lands in eval/data/coco_everyday/ (gitignored): images/, labels/
(COCO80 class ids, one box per line) and manifest.json. Re-running skips
what is already there. The images are Flickr photographs under Creative
Commons licences and the annotations are CC BY 4.0; see
cocodataset.org/#termsofuse.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "coco_everyday"
LABELS_URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/coco2017labels.zip"
IMAGE_URL = "http://images.cocodataset.org/val2017/{name}.jpg"

# COCO80 ids of the indoor everyday classes: bags, tableware, food, furniture,
# appliances, desk things, bathroom things.
INDOOR = {24, 25, 26, 28, 39, 40, 41, 42, 43, 44, 45, 46, 47, 56, 57, 58, 59, 60, 61,
          62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 78, 79}


def fetch(url: str, dest: Path, what: str) -> None:
    if dest.exists():
        print(f"{what}: already here ({dest.name})")
        return
    print(f"{what}: downloading {url}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "med-i-glasses-eval/1.0"})
    partial = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(request, timeout=120) as response, open(partial, "wb") as out:
        shutil.copyfileobj(response, out)
    partial.rename(dest)
    print(f"{what}: {dest.stat().st_size / 1e6:.1f} MB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    labels_zip = DATA / "coco2017labels.zip"
    fetch(LABELS_URL, labels_zip, "labels")

    labels_dir = DATA / "labels"
    labels_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(labels_zip) as archive:
        members = [m for m in archive.namelist()
                   if m.startswith("coco/labels/val2017/") and m.endswith(".txt")]
        for member in members:
            out = labels_dir / Path(member).name
            if not out.exists():
                out.write_bytes(archive.read(member))
    print(f"labels: {len(members)} val2017 files")

    candidates = []
    for path in sorted(labels_dir.glob("*.txt")):
        ids = {int(line.split()[0]) for line in path.read_text().split("\n") if line.strip()}
        if ids & INDOOR:
            candidates.append(path.stem)
    rng = random.Random(args.seed)
    rng.shuffle(candidates)
    chosen = sorted(candidates[: args.count])
    print(f"images: {len(candidates)} val photos hold an indoor everyday class; taking {len(chosen)}")

    images_dir = DATA / "images"
    images_dir.mkdir(exist_ok=True)

    def get(name: str) -> str:
        dest = images_dir / f"{name}.jpg"
        if dest.exists():
            return "kept"
        for attempt in range(2):
            try:
                fetch_quiet(IMAGE_URL.format(name=name), dest)
                return "fetched"
            except Exception as err:  # noqa: BLE001 - one retry, then report
                last = err
        return f"failed: {last}"

    def fetch_quiet(url: str, dest: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "med-i-glasses-eval/1.0"})
        partial = dest.with_suffix(".part")
        with urllib.request.urlopen(request, timeout=60) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        partial.rename(dest)

    outcomes: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index, outcome in enumerate(pool.map(get, chosen), 1):
            key = outcome.split(":")[0]
            outcomes[key] = outcomes.get(key, 0) + 1
            if index % 50 == 0 or index == len(chosen):
                print(f"  {index}/{len(chosen)}  {outcomes}", flush=True)

    present = sorted(p.stem for p in images_dir.glob("*.jpg"))
    (DATA / "manifest.json").write_text(json.dumps({
        "source": "COCO val2017 slice, labels from " + LABELS_URL,
        "seed": args.seed,
        "requested": len(chosen),
        "images": present,
    }, indent=2))
    total_mb = sum(p.stat().st_size for p in images_dir.glob("*.jpg")) / 1e6
    print(f"done: {len(present)} photos, {total_mb:.0f} MB, in {DATA}")
    return 0 if outcomes.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
