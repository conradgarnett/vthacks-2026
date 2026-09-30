#!/usr/bin/env python3
"""Write a corpus to disk as JPEG frames plus a JSONL manifest.

The corpora are generated, not stored, so this repository holds code rather
than gigabytes of images: the same seed gives the same bytes on any machine,
and a fix to a generator improves every future run instead of leaving a stale
archive behind. Run this when you want files on disk -- to train something, to
look at what is actually being scored, or to score a reader that takes paths
rather than a Python call.

    python make_samples.py signage
    python make_samples.py medicine --holdout --out out/medicine
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

MEMORY

Samples are built and written in chunks rather than all at once. An earlier
version built the whole corpus first and wrote it afterwards, which at 744
signage samples held about half a gigabyte of JPEG in memory and got the
process killed. Chunking keeps the footprint flat however large `--n` gets,
which is the point: this should not have a size ceiling.

Chunking has to carry an offset. The allergen, symbol and texture builders
step their lists by index, so restarting at 0 for every chunk would re-render
the first few statements over and over and never reach the tail of the list --
silently, with the manifest looking healthy.
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
import textures  # noqa: E402
import vocabulary as V  # noqa: E402
import webcam  # noqa: E402
from PIL import Image  # noqa: E402

# Samples held in memory at once. Large enough that per-chunk overhead is
# irrelevant, small enough that a chunk of full-frame bursts is tens of MB.
CHUNK = 48


def _signage(args, n, offset, seed) -> list[dict]:
    from typefaces import font_set

    samples = corpus.build_corpus(
        n=n, seed=seed, frames=args.frames, fonts=font_set(args.fonts)
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


def _textureless(args, n, offset, seed) -> list[dict]:
    # No `text` key at all, deliberately. An empty string reads as "the truth
    # is the empty string"; the absent key says this sample has no
    # transcription because there is nothing to transcribe, and any output on
    # it was invented.
    return [
        {"frames": s["frames"], "kind": s["kind"], "no_text": True}
        for s in textures.build_labelled(
            n=n, seed=seed, frames=args.frames, offset=offset
        )
    ]


def _medicine(args, n, offset, seed) -> list[dict]:
    samples = (
        medicine.build_holdout_corpus(n=n, frames=args.frames)
        if args.holdout
        else medicine.build_medicine_corpus(n=n, seed=seed, frames=args.frames)
    )
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


def _labels(args, n, offset, seed) -> list[dict]:
    samples = product_labels.build_packaging_corpus(
        n=n, seed=seed, frames=args.frames, offset=offset
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


def _symbols(args, n, offset, seed) -> list[dict]:
    samples = product_labels.build_symbol_corpus(
        n=n, seed=seed, frames=args.frames, offset=offset
    )
    out = []
    for s in samples:
        frames = s["frames"] if isinstance(s, dict) else s
        entry = {"frames": frames, "no_text": True}
        if isinstance(s, dict) and "kind" in s:
            entry["kind"] = s["kind"]
        out.append(entry)
    return out


def _allergens(args, n, offset, seed) -> list[dict]:
    samples = allergens.build_allergen_corpus(
        n=n, seed=seed, frames=args.frames, offset=offset
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


def _fonts(args, n, offset, seed) -> list[dict]:
    # One sample per (face, phrase); `n` does not apply, so this runs once.
    samples = fonts.build_font_corpus(
        seed=seed, frames=args.frames, bundled=args.fonts != "platform"
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

# `n` is not a parameter of the font benchmark: it is one sample per face and
# phrase, and chunking it would just repeat the whole set.
UNCHUNKED = {"fonts"}


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
    parser.add_argument("--n", type=int, default=None, help="samples (vocabulary-sized)")
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
    # independent: several builders step their lists by index, so an `n` below
    # the list length silently never renders the tail of it -- at n=40 against
    # 107 statements, 67 statements are simply absent and nothing says so.
    # Roughly 2-3x the list is where every entry appears and capture variation
    # is well sampled.
    #
    # These are deliberately larger than the generator defaults, which stay
    # small so the project's eval loop stays fast. Generating is cheap;
    # reading 200 labels with an OCR engine is not.
    defaults = {
        "signage": (3 * len(V.SIGNAGE), 11),
        "textureless": (4 * len(textures.KINDS), 23),
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

    camera = webcam.TIERS[args.camera] if args.camera else None
    manifest = out / "manifest.jsonl"
    build = CORPORA[args.corpus]

    written = frames_written = chunk_index = 0
    truths: set[str] = set()

    print(f"generating {args.corpus} ...", file=sys.stderr, flush=True)

    with manifest.open("w") as handle:
        while True:
            if args.corpus in UNCHUNKED:
                take = 0
            else:
                take = min(CHUNK, args.n - written)
                if take <= 0:
                    break

            # Derived from the run's seed so the whole output stays
            # reproducible from (corpus, n, seed), while each chunk draws
            # differently rather than repeating the one before.
            chunk_seed = args.seed * 7919 + chunk_index
            samples = build(args, take, written, chunk_seed)

            for i, sample in enumerate(samples, start=written):
                paths = []
                for j, jpeg in enumerate(sample.pop("frames")):
                    if camera is not None:
                        jpeg = _recapture(jpeg, camera, i * 100 + j)
                    name = f"{i:05d}_{j}.jpg"
                    (out / name).write_bytes(jpeg)
                    paths.append(name)
                    frames_written += 1

                record = {"id": f"{args.corpus}-{i:05d}", "frames": paths}
                record.update(sample)
                if camera is not None:
                    record["camera"] = camera.name
                handle.write(json.dumps(record) + "\n")

                # Order matters: an allergen record carries both `statement`
                # and `product_name`, and the statement is the thing under
                # test. Checking product_name first reported 20 distinct
                # truths for a corpus of 71 statements -- the diversity of the
                # front of the pack, not of the small print.
                for key in ("statement", "text", "drug", "product_name"):
                    if key in record:
                        truths.add(record[key])
                        break

            written += len(samples)
            chunk_index += 1
            del samples  # the chunk's JPEGs go out of scope here

            print(f"  {written} samples", file=sys.stderr, flush=True)
            if args.corpus in UNCHUNKED or written >= args.n:
                break

    print(
        f"{written} samples, {frames_written} frames -> {out}/\n"
        f"manifest: {manifest}",
        file=sys.stderr,
    )
    if truths:
        print(
            f"distinct ground truth: {len(truths)}"
            f"  ({written / len(truths):.1f} captures each)",
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
