#!/usr/bin/env python3
"""Fetch real photographs with real ground truth, from open databases.

    python fetch_real.py openfoodfacts --n 500

The synthetic corpora measure a reader under conditions you control. They
cannot tell you whether the simulation is right. This fetches the other half:
actual photographs of actual packaging, taken by members of the public on
whatever phone they had, with ground truth attached by the database rather
than by us.

WHY OPEN FOOD FACTS AND NOT AN IMAGE SEARCH

An evaluation corpus needs a transcription for every image. Images scraped
from a search engine have none, so they cannot score a reader -- someone has
to sit and type out what each one says first, and until they do the images
are worth nothing for measurement. Open Food Facts already holds, per product:

    selected_images.ingredients   a photo of the ingredients panel
    ingredients_text              what that panel says, transcribed
    allergens_tags                allergens stated on the label
    traces_tags                   precautionary "may contain" allergens

That last pair is the part worth stopping on. The database keeps stated
allergens and trace warnings in *separate fields*, which is the same
distinction the allergen matcher has to make between "CONTAINS MILK" and "MAY
CONTAIN TRACES OF MILK". It is free, correct, human-checked ground truth for
the exact thing the synthetic allergen corpus is trying to approximate.

LICENSING, AND WHY NOTHING IS COMMITTED

Open Food Facts data is ODbL 1.0; the photographs are CC BY-SA 3.0. Both are
share-alike, which does not compose with this repository's MIT licence. So
this script downloads into `data/`, which is gitignored, and nothing fetched
is ever committed or redistributed. Attribution for anything you publish from
it goes to Open Food Facts and its contributors.

PACING, AND A MISTAKE WORTH NOT REPEATING

The search endpoint is the expensive one and is limited to roughly 10
requests a minute. `--pace` spaces them at 10 seconds by default; do not
lower it, and do not run several copies of this script at once.

Running a sweep of 112 category-by-country combinations back to back got
search refused with 503 for every query, including a one-field one, while
the product endpoint kept answering 200 -- which is what being throttled
looks like from the outside, and is easy to misread as the API being down.
It recovers on its own after a rest. The fix is to fetch less often over a
longer period, not to retry harder: this is a free database run by
volunteers, and the photographs and the allergen tags in it were put there
by people one at a time.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = (
    "assistive-ocr-corpus/1.0 (research; "
    "https://github.com/conradgarnett/vthacks-2026)"
)
SEARCH = "https://world.openfoodfacts.org/api/v2/search"

# Open Food Facts allergen tags -> the nine categories the matcher knows.
# Tags outside this map (celery, sulphites, lupin) are real allergens the
# profile has no category for; they are dropped rather than guessed at, and
# `unmapped` in the manifest records that it happened so a silent gap in the
# ground truth cannot be mistaken for a clean label.
ALLERGEN_MAP = {
    "en:gluten": "gluten",
    "en:milk": "dairy",
    "en:eggs": "egg",
    "en:fish": "fish",
    "en:crustaceans": "shellfish",
    "en:molluscs": "shellfish",
    "en:peanuts": "peanut",
    "en:nuts": "tree nut",
    "en:soybeans": "soy",
    "en:sesame-seeds": "sesame",
    "en:mustard": "mustard",
}


def get(url: str, params: dict | None = None, retries: int = 6) -> dict:
    """GET with backoff.

    Open Food Facts returns 503 intermittently under load -- the same query
    succeeds, then fails, then succeeds again within a minute. It is not a
    bad request and not a block, so it is worth waiting out rather than
    reporting as an error.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            # Rebuilt each attempt rather than reused: a Request that has
            # already been through a failed open carries state from it.
            # Accept is not optional -- without it Open Food Facts answers
            # 401 or 503 rather than JSON, which reads like a block and is
            # not one.
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            code = getattr(exc, "code", None)
            if attempt == retries - 1:
                print(
                    f"  gave up after {retries} attempts: {exc}",
                    file=sys.stderr, flush=True,
                )
                raise
            wait = min(90, 8 * 2 ** attempt)
            print(
                f"  attempt {attempt + 1}/{retries} failed"
                f"{f' (HTTP {code})' if code else f': {exc}'}"
                f"; waiting {wait}s",
                file=sys.stderr, flush=True,
            )
            time.sleep(wait)
    return {}


def download(url: str, path: Path) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError):
        return False
    if len(data) < 2000:  # a placeholder or an error page, not a photograph
        return False
    path.write_bytes(data)
    return True


def code_path(code: str) -> str:
    """Open Food Facts' directory layout for a barcode.

    Codes of 8 digits or fewer sit in a directory of their own name; longer
    ones are zero-padded to 13 and split 3/3/3/rest, so 5025125000006 lives
    at 502/512/500/0006.
    """
    if len(code) <= 8:
        return code
    padded = code.zfill(13)
    return f"{padded[:3]}/{padded[3:6]}/{padded[6:9]}/{padded[9:]}"


def image_url(code: str, entry: dict, size: str) -> str | None:
    """Build the photo URL from a search result's `images` entry.

    The search endpoint will not return `selected_images` -- it silently
    drops that field whenever it is asked for alongside others, answering
    200 with the field simply absent, which looks like products having no
    photo rather than like an API limitation. `images` does come back, and
    carries the revision and the available renditions, so the URL is built
    here instead of asking for it. This avoids a second request per product.
    """
    rev = entry.get("rev")
    if not rev:
        return None
    available = entry.get("sizes", {})
    # Fall back through the renditions that exist: 800 is not generated for
    # every image, and asking for a missing one returns a 404 page that is
    # small enough to look like a failed download.
    for candidate in (size, "full", "400", "200"):
        if candidate in available:
            return (
                "https://images.openfoodfacts.org/images/products/"
                f"{code_path(code)}/ingredients_{entry['lang']}"
                f".{rev}.{candidate}.jpg"
            )
    return None


def categories(tags: list[str]) -> tuple[set[str], list[str]]:
    """Map OFF tags to our categories, keeping what did not map."""
    mapped, unmapped = set(), []
    for tag in tags or []:
        if tag in ALLERGEN_MAP:
            mapped.add(ALLERGEN_MAP[tag])
        elif tag.startswith("en:"):
            unmapped.append(tag)
        # Non-en: tags are the same allergen in another language
        # ("fr:avoine"); they duplicate an en: tag when one exists and are
        # not worth guessing at when one does not.
    return mapped, unmapped


def openfoodfacts(args) -> int:
    out = Path(args.out or "data/openfoodfacts")
    out.mkdir(parents=True, exist_ok=True)

    fields = ",".join([
        "code", "product_name", "brands", "allergens_tags", "traces_tags",
        "ingredients_text", "images", "countries_tags",
    ])

    print(
        f"Open Food Facts: up to {args.n} products with a selected "
        f"ingredients photo\n  data ODbL 1.0, photos CC BY-SA 3.0 -- "
        f"fetched, never redistributed\n",
        file=sys.stderr,
    )

    written = skipped = 0
    manifest = out / "manifest.jsonl"
    page = 1

    # Appended, not truncated, and already-fetched barcodes are skipped, so
    # several runs accumulate into one directory. That is the way to get past
    # the deep-paging wall: fetch each country separately into the same --out
    # rather than paging one country until the server gives up.
    seen: set[str] = set()
    if manifest.exists():
        with manifest.open() as handle:
            for line in handle:
                try:
                    seen.add(json.loads(line)["id"].removeprefix("off-"))
                except (json.JSONDecodeError, KeyError):
                    continue
        if seen:
            print(f"  {len(seen)} already in {manifest.name}; adding to it",
                  file=sys.stderr)

    with manifest.open("a") as handle:
        while written < args.n:
            # page_size 20, not 100. The two are not independent of `fields`:
            # 100 products carrying selected_images is heavy enough that the
            # server gives up and answers 503 (sometimes 401), while the same
            # query at 20 returns 200 every time. It presents as rate
            # limiting or a blocked key and is neither -- it is response
            # weight. Raising this back to 100 breaks the fetcher.
            params = {
                "states_tags_en": "ingredients-photo-selected",
                "countries_tags_en": args.country,
                # Optional second axis. Country alone runs out at the
                # deep-paging wall around 200 products; adding a category
                # makes each query a different shallow search instead of one
                # deep one, which is how the set gets past that. It is also
                # the only way to balance the allergen mix -- a broad sweep
                # comes back two thirds gluten and dairy, with one shellfish
                # in a thousand, because that is what the shelf looks like.
                **({"categories_tags_en": args.category} if args.category else {}),
                "fields": fields,
                "page_size": 20,
                "page": page,
            }
            # Deep pages of a filtered search are expensive server-side, so
            # failures get likelier the further in this goes -- around page
            # 10 the retries stop being enough. Everything fetched so far is
            # already on disk and usable, so stop and report it rather than
            # raising and making a partial success look like a failure. To
            # go deeper, fetch several countries into one directory instead:
            # each stays on shallow pages.
            try:
                payload = get(SEARCH, params)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                print(
                    f"\n  search failed at page {page}; stopping with "
                    f"{written} products already fetched.\n"
                    f"  For more, run again with --country united-states "
                    f"(or france, germany) into the same --out.",
                    file=sys.stderr,
                )
                break

            products = payload.get("products", [])
            if not products:
                print("  no more products", file=sys.stderr)
                break

            for product in products:
                if written >= args.n:
                    break
                code = product.get("code")
                if not code or code in seen:
                    continue
                seen.add(code)

                # `images` is keyed "ingredients_<lang>"; prefer the language
                # asked for and take any other rather than skip a usable
                # photo.
                images = product.get("images") or {}
                key = f"ingredients_{args.lang}"
                if key not in images:
                    key = next(
                        (k for k in images if k.startswith("ingredients_")), None
                    )
                text = (product.get("ingredients_text") or "").strip()
                if not key or not text:
                    skipped += 1
                    continue

                entry = dict(images[key])
                entry["lang"] = key.split("_", 1)[1]
                # 400 px is often too small to be a fair test: an engine with
                # a frame-relative minimum text height fails on it for
                # reasons that have nothing to do with the engine.
                url = image_url(code, entry, args.size)
                if not url:
                    skipped += 1
                    continue

                name = f"{code}.jpg"
                if not download(url, out / name):
                    skipped += 1
                    continue

                stated, unmapped = categories(product.get("allergens_tags"))
                traces, _ = categories(product.get("traces_tags"))

                # Framing varies enormously here -- some contributors upload
                # a tight crop of the panel, others the whole pack on a
                # kitchen table. That decides the text-height-to-frame ratio,
                # which is the variable an engine with a frame-relative
                # minimum text height actually keys on, so a score over a
                # mixed set means little unless it can be split by it.
                # Recorded per image rather than left for the reader to
                # rediscover.
                entry_sizes = entry.get("sizes", {})
                chosen = url.rsplit(".", 2)[-2]
                shape = entry_sizes.get(chosen, {})

                handle.write(json.dumps({
                    "id": f"off-{code}",
                    "frames": [name],
                    "source": "openfoodfacts",
                    "licence": "CC BY-SA 3.0",
                    "url": f"https://world.openfoodfacts.org/product/{code}",
                    "product_name": product.get("product_name") or "",
                    "brands": product.get("brands") or "",
                    # The transcription of the panel: OCR ground truth.
                    "ingredients_text": text,
                    # Stated on the label -- the matcher must report these.
                    "allergens": sorted(stated),
                    # Precautionary -- real, but never with the same
                    # certainty as a CONTAINS line.
                    "traces": sorted(traces),
                    "unmapped_allergen_tags": unmapped,
                    # Which query produced this row. Worth keeping: a set
                    # built from several sweeps is not a random sample of the
                    # shelf, and anyone scoring against it should be able to
                    # see how it was assembled.
                    "query_country": args.country,
                    "query_category": args.category,
                    "width": shape.get("w"),
                    "height": shape.get("h"),
                    # A wide, short image is a crop of the panel; a portrait
                    # one is usually the whole pack, with the print much
                    # smaller relative to the frame.
                    "aspect": (
                        round(shape["w"] / shape["h"], 2)
                        if shape.get("w") and shape.get("h") else None
                    ),
                }) + "\n")
                written += 1
                if written % 25 == 0:
                    print(f"  {written}/{args.n}", file=sys.stderr, flush=True)

            page += 1
            if written < args.n:
                time.sleep(args.pace)

    print(
        f"\n{written} products -> {out}/\n"
        f"manifest: {manifest}\n"
        f"skipped {skipped} (no photo, no transcription, or download failed)",
        file=sys.stderr,
    )
    if written:
        with manifest.open() as handle:
            records = [json.loads(line) for line in handle]
        with_allergens = sum(1 for r in records if r["allergens"])
        with_traces = sum(1 for r in records if r["traces"])
        unmapped = sum(1 for r in records if r["unmapped_allergen_tags"])
        print(
            f"\n  {with_allergens} state at least one allergen\n"
            f"  {with_traces} carry a trace warning (the hedged case)\n"
            f"  {len(records) - with_allergens} state none -- reporting one "
            f"on these is an invention\n"
            f"  {unmapped} carry an allergen with no category here "
            f"(celery, sulphites, lupin)",
            file=sys.stderr,
        )
    return 0


SOURCES = {"openfoodfacts": openfoodfacts}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("--n", type=int, default=200, help="products to fetch")
    parser.add_argument("--out", default=None)
    parser.add_argument("--pace", type=float, default=10.0,
                        help="seconds between search requests (default 10, "
                             "~6/min). Do not lower it")
    parser.add_argument("--category", default=None,
                        help="OFF category tag, e.g. seafood, cheeses, "
                             "breakfast-cereals; combine with --country to "
                             "stay on shallow pages and to balance allergens")
    parser.add_argument("--country", default="united-kingdom",
                        help="OFF country tag, e.g. united-states, france")
    parser.add_argument("--lang", default="en", help="ingredients photo language")
    parser.add_argument("--size", default="full",
                        choices=["400", "full"],
                        help="image rendition; 400 is too small to be fair")
    args = parser.parse_args()
    return SOURCES[args.source](args)


if __name__ == "__main__":
    raise SystemExit(main())
