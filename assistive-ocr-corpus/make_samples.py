#!/usr/bin/env python3
"""Write a corpus to disk as JPEG frames plus a JSONL manifest.

The corpora are generated, not stored, so this repository holds code rather
than gigabytes of images: the same seed gives the same bytes on any machine,
and a fix to a generator improves every future run instead of leaving a stale
archive behind. Run this when you want files on disk -- to train something, to
look at what is actually being scored, or to score a reader that takes paths
rather than a Python call.

    python make_samples.py signage --n 60 --out out/signage
    python make_samples.py medicine --holdout --out out/medicine_holdout
    python make_samples.py allergens --camera cheap --out out/allergens_cheap

Each sample is a burst of frames of one scene. A burst is not padding: a
reader that pools several frames does better than one reading a single frame,
and the three-frame default is what the source project measured with. Scoring
one frame per sample is a different measurement -- a legitimate one, but say
which you did.

  --camera phone|webcam|cheap  re-capture through the fixed-focus camera model
                               (webcam.py). Without it, each corpus uses its
                               own capture path, which models a phone.

A note on --camera: it stacks a second capture on top of the corpus's own,
so it is a way to ask "how much worse does a cheap lens make this", not a
clean phone-versus-webcam comparison. For the clean version, render with the
generators and call `webcam.through_camera` on the unblurred image yourself.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "generators"))

import allergens  # noqa: E402
import corpus  # noqa: E402
import fonts  # noqa: E402
import medicine  # noqa: E402
import product_labels  # noqa: E402
import webcam  # noqa: E402
from PIL import Image  # noqa: E402


def _signage(args) -> list[dict]:
    from typefaces import font_set

    samples = corpus.build_corpus(
        n=args.n, seed=args.seed, frames=args.frames, fonts=font_set(args.fonts)
    )
    return [
        {
            "frames": s.frames,
            "text": s.truth,
            "font": s.font,
            "text_height_pct": s.height_pct,
        }
        for s in samples
    ]


def _textureless(args) -> list[dict]:
    return [
        # No `text` key at all, deliberately. An empty string reads as "the
        # truth is the empty string"; the absent key says this sample has no
        # transcription because there is nothing to transcribe, and any output
        # on it was invented.
        {"frames": frames, "no_text": True}
        for frames in corpus.build_textureless_corpus(n=args.n, seed=args.seed)
    ]


def _medicine(args) -> list[dict]:
    build = (
        medicine.build_holdout_corpus
        if args.holdout
        else lambda n, frames: medicine.build_medicine_corpus(
            n=n, seed=args.seed, frames=frames
        )
    )
    samples = build(n=args.n, frames=args.frames)
    return [
        {
            "frames": s["frames"],
            "drug": s["drug"],
            "strength": s["strength"],
            "directions": s["directions"],
            "warning": s["warning"],
            "amber_vial": s["amber"],
            "scale": s["scale"],
            "split": "holdout" if args.holdout else "tuning",
        }
        for s in samples
    ]


def _labels(args) -> list[dict]:
    samples = product_labels.build_packaging_corpus(
        n=args.n, seed=args.seed, frames=args.frames
    )
    return [
        {
            "frames": s["frames"],
            "product_name": s["truth"],
            "font": s["font"],
            "curved": s["curved"],
            "scale": s["scale"],
        }
        for s in samples
    ]


def _symbols(args) -> list[dict]:
    samples = product_labels.build_symbol_corpus(
        n=args.n, seed=args.seed, frames=args.frames
    )
    # build_symbol_corpus returns bare frame lists in some versions and dicts
    # in others; accept both rather than depend on which.
    out = []
    for s in samples:
        frames = s["frames"] if isinstance(s, dict) else s
        entry = {"frames": frames, "no_text": True}
        if isinstance(s, dict) and "kind" in s:
            entry["kind"] = s["kind"]
        out.append(entry)
    return out


def _allergens(args) -> list[dict]:
    samples = allergens.build_allergen_corpus(
        n=args.n, seed=args.seed, frames=args.frames
    )
    return [
        {
            "frames": s["frames"],
            "statement": s["statement"],
            "allergens": sorted(s["truth"]),
            "hedged": s["hedged"],
            "product_name": s["name"],
            "font": s["font"],
            "scale": s["scale"],
            "curved": s["curved"],
        }
        for s in samples
    ]


def _fonts(args) -> list[dict]:
    samples = fonts.build_font_corpus(
        seed=args.seed, frames=args.frames, bundled=args.fonts != "platform"
    )
    return [
        {
            "frames": s["frames"],
            "text": s["truth"],
            "font": s["font"],
            "family": s["family"],
        }
        for s in samples
    ]


CORPORA = {
    "signage": _signage,
    "textureless": _textureless,
    "medicine": _medicine,
    "labels": _labels,
    "symbols": _symbols,
    "allergens": _allergens,
    "fonts": _fonts,
}

# Corpora whose samples contain no text. Anything a reader emits on these was
# invented, and a run that omits them cannot report a hallucination rate.
NO_TEXT = {"textureless", "symbols"}


def _recapture(jpeg: bytes, camera: webcam.Camera, index: int) -> bytes:
    """Put an already-captured frame through a second, worse camera."""
    image = Image.open(io.BytesIO(jpeg))
    return webcam.through_camera(image, camera, random.Random(index))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("corpus", choices=sorted(CORPORA))
    parser.add_argument("--out", default=None, help="output directory")
    parser.add_argument("--n", type=int, default=None, help="samples (corpus default)")
    parser.add_argument("--seed", type=int, default=None, help="corpus default if unset")
    parser.add_argument("--frames", type=int, default=3, help="frames per sample")
    parser.add_argument(
        "--camera",
        choices=sorted(webcam.TIERS),
        default=None,
        help="re-capture every frame through this camera model",
    )
    parser.add_argument(
        "--fonts",
        choices=["platform", "bundled", "all"],
        default="bundled",
        help="bundled (default, identical everywhere) or this machine's fonts",
    )
    parser.add_argument("--holdout", action="store_true", help="medicine: held-out seeds")
    args = parser.parse_args()

    # Sized from the vocabulary rather than fixed, because the two are not
    # independent: the allergen and signage builders step through their lists
    # by index, so an `n` below the list length silently never renders the
    # tail of it -- at n=40 against 71 statements, 31 statements are simply
    # absent and nothing says so. Roughly 2-3x the list is where every entry
    # appears and capture variation is well sampled.
    #
    # These are deliberately larger than the generator defaults, which stay
    # small so the project's eval loop stays fast. Generating is cheap;
    # reading 200 labels with an OCR engine is not.
    import vocabulary as V

    defaults = {
        "signage": (3 * len(V.SIGNAGE), 11),
        "textureless": (32, 23),
        "medicine": (2 * len(V.DRUGS), 101),
        "labels": (2 * len(V.PRODUCT_TEXT), 31),
        "symbols": (48, 37),
        "allergens": (3 * len(V.STATEMENTS), 41),
        "fonts": (0, 41),
    }
    default_n, default_seed = defaults[args.corpus]
    if args.n is None:
        args.n = default_n
    if args.seed is None:
        args.seed = default_seed

    out = Path(args.out or f"out/{args.corpus}")
    out.mkdir(parents=True, exist_ok=True)

    print(f"generating {args.corpus} ...", file=sys.stderr, flush=True)
    samples = CORPORA[args.corpus](args)

    camera = webcam.TIERS[args.camera] if args.camera else None
    manifest = out / "manifest.jsonl"
    written = 0

    with manifest.open("w") as handle:
        for i, sample in enumerate(samples):
            frames = sample.pop("frames")
            paths = []
            for j, jpeg in enumerate(frames):
                if camera is not None:
                    jpeg = _recapture(jpeg, camera, i * 100 + j)
                name = f"{i:04d}_{j}.jpg"
                (out / name).write_bytes(jpeg)
                paths.append(name)
                written += 1
            record = {"id": f"{args.corpus}-{i:04d}", "frames": paths}
            record.update(sample)
            if camera is not None:
                record["camera"] = camera.name
            handle.write(json.dumps(record) + "\n")

    # Report distinct ground truth alongside the sample count, because they
    # are what the two numbers actually mean: 400 samples over 135 phrases is
    # 400 photographs of 135 things, and a reader that memorised the 135
    # scores the same as one that generalises.
    # Order matters: an allergen record carries both `statement` and
    # `product_name`, and the statement is the thing under test. Checking
    # product_name first reported 20 distinct truths for a corpus of 71
    # statements -- the diversity of the front of the pack, not of the small
    # print, which is the opposite of what this corpus measures.
    truths = set()
    with manifest.open() as handle:
        for line in handle:
            record = json.loads(line)
            for key in ("statement", "text", "drug", "product_name"):
                if key in record:
                    truths.add(record[key])
                    break

    print(
        f"{len(samples)} samples, {written} frames -> {out}/\n"
        f"manifest: {manifest}",
        file=sys.stderr,
    )
    if truths:
        print(
            f"distinct ground truth: {len(truths)}"
            f"  ({len(samples) / len(truths):.1f} captures each)",
            file=sys.stderr,
        )
    if args.corpus in NO_TEXT:
        print(
            "these samples contain no text: any output on them is invented",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
