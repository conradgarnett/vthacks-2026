"""Score the detector: what the vocabulary costs and what it invents.

    cd visionos && PYTHONPATH=. .venv/bin/python eval/run_detect_eval.py [--json out.json] [--limit N]

Three measurements, so a vocabulary change is a number rather than an
argument:

  timing     median detect time on one frame at 640 px (the live loop) and
             at 1280 px (a scan), on this machine with this vocabulary.
  textures   the OCR eval's no-text surfaces (carpet, brick, foliage,
             blinds). Nothing is in them, so every detection is invented.
  everyday   if eval/data/coco_everyday exists (fetch_everyday.py puts it
             there), recall and precision at IoU 0.5 against COCO's labels
             for the classes the vocabulary shares with COCO, per class and
             overall, plus predictions that land on one object twice, which
             is what near-duplicate labels ("table" and "desk" on the same
             table) look like.

Save the report with --json, edit the vocabulary, run again, and compare the
two files: that is the review a new class has to pass.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import build_textureless_corpus

from backend.config import get_settings
from backend.perception.detector import Detector
from backend.perception.vocabulary import CLASS_NAMES

DATA = Path(__file__).resolve().parent / "data" / "coco_everyday"

COCO80 = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
]

# COCO's name for a thing, and every vocabulary label that counts as having
# found it. Only what COCO labels exhaustively can be scored: a door cannot.
COCO_TO_OURS: dict[str, list[str]] = {
    "person": ["person"], "bicycle": ["bicycle"], "car": ["car"], "motorcycle": ["motorcycle"],
    "bus": ["bus"], "truck": ["truck"], "traffic light": ["traffic light"],
    "fire hydrant": ["fire hydrant"], "stop sign": ["stop sign"], "parking meter": ["parking meter"],
    "bench": ["bench"], "cat": ["cat"], "dog": ["dog"], "backpack": ["backpack"],
    "umbrella": ["umbrella"], "handbag": ["handbag"], "suitcase": ["suitcase"],
    "bottle": ["bottle", "pill bottle"], "wine glass": ["drinking glass", "wine glass"],
    "cup": ["cup"], "fork": ["fork"], "knife": ["knife"], "spoon": ["spoon"], "bowl": ["bowl"],
    "banana": ["banana"], "apple": ["apple"], "chair": ["chair", "armchair", "stool"],
    "couch": ["couch"], "potted plant": ["potted plant"], "bed": ["bed"],
    "dining table": ["dining table", "table", "desk", "coffee table"], "toilet": ["toilet"],
    "tv": ["tv"], "laptop": ["laptop"], "mouse": ["computer mouse"], "remote": ["remote"],
    "keyboard": ["keyboard"], "cell phone": ["cell phone"], "microwave": ["microwave"],
    "oven": ["oven", "stove"], "toaster": ["toaster"], "sink": ["sink"],
    "refrigerator": ["refrigerator"], "book": ["book"], "clock": ["clock"], "vase": ["vase"],
    "scissors": ["scissors"], "hair drier": ["hair dryer"], "toothbrush": ["toothbrush"],
}

IOU_MATCH = 0.5
TIMING_RUNS = {640: 12, 1280: 8}


def decode(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def timing(detector: Detector, frame: np.ndarray) -> dict:
    out = {}
    for imgsz, runs in TIMING_RUNS.items():
        for _ in range(2):
            detector.detect_sync(frame, imgsz)
        times = []
        for _ in range(runs):
            started = time.perf_counter()
            detector.detect_sync(frame, imgsz)
            times.append((time.perf_counter() - started) * 1000)
        times.sort()
        out[str(imgsz)] = {
            "median_ms": round(statistics.median(times), 1),
            "p90_ms": round(times[int(0.9 * (len(times) - 1))], 1),
        }
    return out


def textures(detector: Detector) -> dict:
    frames = [decode(jpeg) for group in build_textureless_corpus(n=8) for jpeg in group]
    invented: dict[str, int] = defaultdict(int)
    frames_with = 0
    for frame in frames:
        found = detector.detect_sync(frame)
        if found:
            frames_with += 1
        for d in found:
            invented[d.label] += 1
    return {
        "frames": len(frames),
        "frames_with_detections": frames_with,
        "by_label": dict(sorted(invented.items(), key=lambda kv: -kv[1])),
    }


def load_everyday(limit: int | None) -> list[tuple[Path, list]]:
    images_dir = DATA / "images"
    if not images_dir.exists():
        return []
    images = sorted(images_dir.glob("*.jpg"))
    if limit:
        images = images[:limit]
    out = []
    for path in images:
        boxes = []
        label_path = DATA / "labels" / (path.stem + ".txt")
        if label_path.exists():
            for line in label_path.read_text().split("\n"):
                parts = line.split()
                if len(parts) < 5:
                    continue
                cls = int(parts[0])
                cx, cy, w, h = (float(v) for v in parts[1:5])
                boxes.append((COCO80[cls], cx, cy, w, h))
        out.append((path, boxes))
    return out


def everyday(detector: Detector, limit: int | None) -> dict | None:
    samples = load_everyday(limit)
    if not samples:
        return None
    scored = {coco: [l for l in ours if l in CLASS_NAMES] for coco, ours in COCO_TO_OURS.items()}
    scored = {coco: ours for coco, ours in scored.items() if ours}
    ours_to_coco = {label: coco for coco, labels in scored.items() for label in labels}

    per: dict[str, dict[str, int]] = defaultdict(lambda: {"gt": 0, "found": 0, "tp": 0, "fp": 0, "dup": 0})
    times = []
    for path, boxes in samples:
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        height, width = frame.shape[:2]
        gts = []
        for coco, cx, cy, bw, bh in boxes:
            if coco not in scored:
                continue
            gts.append([coco, (cx - bw / 2) * width, (cy - bh / 2) * height,
                        (cx + bw / 2) * width, (cy + bh / 2) * height, False])
            per[coco]["gt"] += 1

        started = time.perf_counter()
        found = detector.detect_sync(frame)
        times.append((time.perf_counter() - started) * 1000)

        for d in sorted(found, key=lambda d: -d.confidence):
            coco = ours_to_coco.get(d.label)
            if coco is None:
                continue  # a door, a sign: COCO has no opinion
            box = (d.box.x1, d.box.y1, d.box.x2, d.box.y2)
            best, best_iou = None, 0.0
            for gt in gts:
                if gt[0] != coco:
                    continue
                value = iou(box, gt[1:5])
                if value > best_iou:
                    best, best_iou = gt, value
            if best is None or best_iou < IOU_MATCH:
                per[coco]["fp"] += 1
            elif best[5]:
                per[coco]["dup"] += 1
            else:
                best[5] = True
                per[coco]["tp"] += 1
                per[coco]["found"] += 1

    gt = sum(v["gt"] for v in per.values())
    found = sum(v["found"] for v in per.values())
    tp = sum(v["tp"] for v in per.values())
    fp = sum(v["fp"] for v in per.values())
    dup = sum(v["dup"] for v in per.values())
    return {
        "images": len(samples),
        "detect_ms_median": round(statistics.median(times), 1) if times else None,
        "recall": round(found / gt, 3) if gt else None,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "gt": gt, "tp": tp, "fp": fp, "duplicates": dup,
        "classes": {
            coco: dict(v) for coco, v in sorted(per.items(), key=lambda kv: -kv[1]["gt"])
        },
    }


def rule(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, help="save the report here")
    parser.add_argument("--limit", type=int, help="everyday images to score (default all)")
    args = parser.parse_args()

    settings = get_settings()
    detector = Detector(settings)
    started = time.perf_counter()
    detector.load()
    load_s = time.perf_counter() - started
    print(
        f"detector={settings.open_vocab_weights} device={detector._device} "
        f"classes={len(CLASS_NAMES)} load={load_s:.1f}s on {platform.system()}"
    )

    samples = load_everyday(1)
    frame = cv2.imread(str(samples[0][0])) if samples else decode(build_textureless_corpus(n=1)[0][0])
    report = {
        "classes": len(CLASS_NAMES),
        "device": detector._device,
        "timing": timing(detector, frame),
        "textures": textures(detector),
        "everyday": everyday(detector, args.limit),
    }

    rule("DETECT TIME  (one frame, this machine)")
    for imgsz, stats in report["timing"].items():
        print(f"  {imgsz:>5} px   median {stats['median_ms']:7.1f} ms   p90 {stats['p90_ms']:7.1f} ms")

    tex = report["textures"]
    rule(f"NO-OBJECT SURFACES  (n={tex['frames']} frames of carpet, brick, foliage, blinds)")
    print(f"  frames with an invented object  {tex['frames_with_detections']}/{tex['frames']}  (0 is the only acceptable score)")
    for label, count in tex["by_label"].items():
        print(f"     {label:<20} {count}")

    every = report["everyday"]
    if every is None:
        rule("EVERYDAY OBJECTS")
        print("  no photos yet: run eval/fetch_everyday.py to pull the COCO slice")
    else:
        rule(f"EVERYDAY OBJECTS  (n={every['images']} photos, COCO val2017 labels, IoU 0.5)")
        print(f"  recall     {every['recall']:.0%}   of {every['gt']} labeled things found")
        print(f"  precision  {every['precision']:.0%}   of {every['tp'] + every['fp']} claims were real")
        print(f"  duplicates {every['duplicates']}   second boxes on one thing")
        print(f"  detect     {every['detect_ms_median']} ms median per photo at 640 px")
        print()
        print(f"   {'class':<15} {'gt':>4} {'found':>6} {'fp':>4} {'dup':>4}   recall  precision")
        for coco, v in every["classes"].items():
            if v["gt"] == 0 and v["fp"] == 0:
                continue
            recall = f"{v['found'] / v['gt']:.0%}" if v["gt"] else "   -"
            precision = f"{v['tp'] / (v['tp'] + v['fp']):.0%}" if v["tp"] + v["fp"] else "   -"
            print(f"   {coco:<15} {v['gt']:>4} {v['found']:>6} {v['fp']:>4} {v['dup']:>4}   {recall:>6}  {precision:>9}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2))
        print(f"\nsaved {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
