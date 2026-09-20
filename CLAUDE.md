# VisionOS — notes for whoever works on this next

Assistive "digital senses" platform. `visionos/` is the sight module: a phone
camera feeds a spatial scene model, and the user hears descriptions, answers
and hazard alerts. Hackathon project, several people and agents working in
parallel on separate branches.

The primary user is blind. Two consequences shape most decisions here:

- **Speaking something wrong is worse than saying nothing.** The user cannot
  glance at the sign to check. Silence is unhelpful; confident nonsense is
  misinformation.
- **Nothing visual can be load-bearing.** Every state change has to reach the
  user as speech, a tone, or haptics.

## Constraints that will break the app if violated

These were each found by breaking them. They are not style preferences.

**PyTorch MPS is not thread-safe.** Two threads encoding to one Metal command
buffer kills the process on an assertion — a hard crash, no traceback, no
recovery. It presents as the phone reconnect-looping, not as an error. All
torch inference goes through the single worker in
`backend/perception/runtime.py`. Do not raise `max_workers`.

**Apple Vision must NOT share that thread.** Vision never touches torch's
Metal path, so it is safe alongside — but sharing the thread put reads behind
YOLO and depth for every frame of their own burst. One live read took 19.9 s;
on its own thread the same read is 311 ms. Use `run_ocr`, not `run_inference`.

**The WebSocket must be proxied through Vite.** The page is HTTPS (required —
`getUserMedia` refuses a non-secure origin), so browsers will only open
`wss://`. Running TLS on the backend instead means the phone must accept a
second self-signed cert, and Safari never prompts for one on a WebSocket: it
fails silently and reconnects forever. `vite.config.ts` proxies `/ws`,
`/health`, `/scene`, `/metrics` so everything is same-origin behind one cert.

**Frames are dropped, not queued.** Inference is serialized, so a phone
sending faster than the GPU drains would build an unbounded backlog and every
answer would describe a room the user already left.

## Measured facts — don't re-derive these

| thing | measurement | consequence |
|---|---|---|
| YOLO detect (MPS) | 9–25 ms | runs every frame |
| Depth model | 117 ms | every 6th frame only |
| Apple Vision OCR | 15 ms warm, 315 ms first call | warmed at startup |
| CPU vs MPS detect | 31.8 ms vs 9.0 ms | MPS matters |

**Distance comes from known object heights, not the depth network.** Monocular
depth is relative and unscaled; calibrating it needs a reference, and a wrong
reference means confidently telling someone a wall is 2 m away when it is 5.
Size priors live in `backend/perception/vocabulary.py`.

**Detection is open-vocabulary** (YOLO-World). COCO has no door, no stairs, no
handrail — the three things a blind person most needs found. It is also
*cheaper* than the medium closed-set model (25 ms vs 43 ms).

**Path clearance uses lateral offset, not an angular cone.** A 15° cone is
0.26 m wide at 1 m and 1.3 m at 5 m.

## OCR — read this before changing `backend/ai/ocr.py`

The pipeline is counterintuitive in four ways, each measured:

1. **Vision's minimum text height is a fraction of the FRAME.** A sign across
   a room is invisible regardless of sensor resolution. CER was 1.00 below 2%
   of frame height. Lowering the threshold does not fix it — that is what
   makes it read carpet and foliage as text. Tiling fixes the ratio instead.
2. **Unconditional tiling measures WORSE than none.** A crop re-reads what the
   full frame already got right and attaches a garbled twin ("Departures" →
   "DLpartiirL Departures"). Tiles are gated on the full-frame pass returning
   thin.
3. **Dedupe keys on POSITION, not text similarity.** Two bad reads of one word
   garble differently (~0.6 similar), so text matching emitted both.
4. **Vision's confidence is useless as a filter** — ~0.5 for nearly
   everything, garbage included. Plausibility is judged linguistically in
   `backend/ai/text_quality.py`.

**Two engines, one pipeline.** `TextReader` holds everything above:
preprocessing, tiling, consensus, dedupe, reading order. `AppleVisionOCR`
(macOS) and `RapidOCR` (PaddleOCR on ONNX Runtime, CPU, any OS) override only
`_recognize`. `build_reader()` takes the first engine that loads; `OCR_ENGINE`
in `.env` pins one. A pipeline change must be scored with `eval/` on both
engines where possible — the report names the engine and the font set, and
numbers only compare within the same pair. RapidOCR has no frame-relative
minimum text height (`has_height_floor = False`), so it skips Vision's
second, lower pass; tiling still applies.

## Verifying a change

```bash
cd visionos
.venv/bin/python -m pytest backend/tests -q          # 516 tests (3 Apple-only)
PYTHONPATH=. .venv/bin/python eval/run_ocr_eval.py 60
```

On Windows the venv's interpreter is `.venv/Scripts/python.exe` and
`PYTHONPATH=.` is set with `set` or `$env:`; `python run.py --check` prints
what the machine can do without starting anything.

**Any change to OCR must be scored against `eval/`, before and after.** It
renders signage across 16 system fonts, 8 apparent sizes (1.2%–14% of frame
height), 6 colour schemes, then simulates a hand-held capture: perspective,
motion blur, glare, sensor noise, exposure drift, JPEG artifacts.

Do **not** substitute a "does the output contain the word" check. It passes
"Room 204B" read as "Room 2048", which is how this pipeline once looked fine
while performing badly.

Current baseline to beat, n=60, as of 2026-09-19:

```
mean CER 0.182 · exact match 70% · silent 2% · hallucinated 0/8 · read p50 281 ms
```

Known weak spot: text at 1.2% of frame height (CER ~0.85) — a standard sign at
15+ m, ~14 px tall before blur. Closer to an information floor than a tuning
gap.

## Running it on any machine

`python run.py` (or `make run`) is the one entry point: it creates the venv,
installs backend and client dependencies, finds Node even when the installer
left it off PATH (Windows does), downloads the weights, reports the vision
provider and OCR engine this machine can use, asks whether it will be used
from a phone (HTTPS on the LAN) or a browser on this computer (HTTP on
localhost, which is a secure context), and starts both servers. `--yes`
takes the defaults, `--local` / `--phone` skip the question.

Things learned getting it up on Windows, each of which cost time:

- **Vite must be served over plain HTTP for a browser on the same machine.**
  `VISIONOS_HTTP=1` drops the self-signed certificate; some embedded browsers
  refuse it outright with no way through. The phone still needs HTTPS.
- **A connection is only real once the server has spoken.** Vite's proxy
  accepts the WebSocket before the backend does, so with the backend down
  every attempt looked like a connect-then-drop and the client re-announced
  "I lost connection" every 500 ms. `ws.ts` waits for the first message.
- **ultralytics caches the CLIP text encoder under `<repo>/weights/clip/`**,
  not `~/.cache/clip`, so a pre-seeded `~/.cache/clip/ViT-B-32.pt` is ignored
  and the first backend start downloads 338 MB. `run.py` precaches through
  ultralytics itself so the path matches.
- **Long paths break Python on Windows.** A venv or a test module with a long
  filename under a deep folder fails to import with no useful error; keep the
  checkout somewhere short like `C:/Users/<you>/vthacks-2026`.
- **Python 3.10 runs the whole suite.** The doctor's floor is 3.10, not 3.11.
- **RapidOCR on a CPU: detect small, recognize full.** Its detector scales with
  pixel count (3.3 s on a 1080p frame). Capping the whole read at 1280 px cut
  that to 1.7 s but the recognizer then read downscaled crops and lost the
  spaces between words (CER 0.022 -> 0.043, exact 90% -> 82%). Detection runs
  on the small copy; recognition crops come from the full frame: 657 ms per
  frame, spaces intact. A burst also stops after two agreeing frames on a
  costly engine (`costly_frames`), and the client sends live frames every
  1.5 s instead of 0.7 s when the backend reports `device: cpu`.
- **Benchmarks starve the live app.** Run them at idle priority on a shared
  machine (the scratch `idle_run.py` pattern: `SetPriorityClass` then spawn).
  Corpus rendering is ~5 s per sample and dominates a run; keep it out of
  read timings.

## Working in parallel

Several branches are active (`main-project`, `visionOS-2`, `side-project`).
Merge via PR; do not push to a branch someone else owns, and do not
force-push. When two versions of the same file disagree, run both through
`eval/` and keep the better score — a measurement rather than an argument.

**The channel between agents is the thread on PR #2** (and PR #1 for the
merge itself). Post there after every push: what moved, why, and what is
next. Read it before starting work. Humans relay when needed.

**Lanes, agreed 2026-09-19 in PR #2:**

| lane | owner | files |
|---|---|---|
| perception + backend OCR | `main-project` agent (Conrad's machine) | `backend/ai/ocr.py` pipeline, `backend/ai/text_quality.py`, `backend/perception/*`, `eval/` |
| client + cross-platform runtime | `visionOS-2` agent (Windows machine) | `client/index.html`, `client/src/*.ts`, `RapidOCR` and `build_reader` in `ocr.py`, `backend/config.py`, `backend/doctor.py`, `run.py`, `Makefile` |
| wire protocol | shared | `backend/main.py` and `client/src/ws.ts` change together; announce in the thread first |

Whoever owns a file makes the call. `visionOS-2` merges *into* `main-project`
via PR #1; it is not a replacement.

`HAZARDS_ENABLED` is currently `false` while the detection vocabulary is
tuned; false warnings talk over everything else. The engine is intact and
tested.

## Log

- **2026-09-19, night, visionOS-2:** merged `main-project` at 4037f63
  (Conrad's per-engine gating: RapidOCR lines below 0.70 confidence are
  dropped and the acronym rules relax for a confident engine; his Mac
  measurement keeps the tier and the dedupe veto). Architecture at 361151b:
  `TieredReader(fast, thorough)` when two engines load (Vision then RapidOCR
  on a Mac; RapidOCR alone here) meshes the fast engine's sure lines with
  the thorough read whenever a line is below 0.8; a `PEEK` frame every 4 s
  on CPU feeds a background reader, so Read answers at once from readings
  under 6 s old when every line is >= 0.8, else runs the 3-frame burst with
  exposure turned down; `SCAN` is its own two-frame burst at 1280 px
  (imgsz 1280) and `scene/inference.py` paints the description: setting,
  groups by angle and distance, what a person is doing when person and
  object are both >= 0.8, up to two notable words with a direction, hedged
  glimpses only above 0.8, a landmark reminder; a `detections` event after
  every processed live frame draws red boxes on the preview (live frames
  every 150 ms on CPU, overlay cleared 350 ms after the last processed
  frame); the medication guard speaks numbers that frames agreed on and
  withholds an unconfirmed dose; lexicon context pass and glued-word split;
  the client picks a plugged-in webcam, keys 1, 2, 3 and 0 stand in for
  the watch, a Web Serial button reads the Arduino. Numbers, RapidOCR, Windows,
  0.70 floor: signage n=60 CER 0.031, exact 92%, silent 1/60, no-text
  invented 0/8; bundled fonts n=76 sans 94%, serif 100%, cursive 75%,
  handwriting 83%, novelty 80%; packaging name 12/24, symbols invented
  0/12; prescription labels 40 tuning / 40 held-out: drug name 57% / 72%,
  strength 60% / 78%, dose right 12% / 18%, wrong dose 0%. Conrad's Mac,
  both engines, one corpus set: tier receipts 66% at 1132 ms vs RapidOCR
  alone 66% at 1975 ms vs Vision 36% at 152 ms; medicine drug name 40%,
  dose 25%, wrong 0%. Later that night: size tiers in `vocabulary.py`
  (`scale_of`): hand-held things (cups, bottles, bowls, phones, remotes,
  books, keyboards) are never boxed and are spoken only when asked about
  ("are there any cups on the table"); laptops and bigger are always
  spoken. Detector vocabulary 53 -> 115 classes (`vocabulary.py`, tiered;
  118 added, then apple, banana and handbag cut: apple found 0 of 17 and
  invented 39; class-agnostic NMS so near-synonyms box a thing once),
  measured with the new `eval/run_detect_eval.py`: detect time unchanged
  within noise (640 px ~250 ms, 1280 px ~1 s on this CPU), invented
  objects on 24 blank textures 0 -> 0, and on 300 COCO val2017 photos
  (`eval/fetch_everyday.py`, gitignored) the classes both lists share
  found 59% -> 58% with 10% fewer false claims. Detector floor
  0.20 -> 0.35 (config.py and `.env`, which pins it and overrides the
  code), dangerous classes 0.45, hazard floor 0.45: on the held-out half
  of those photos, claims that were real 54% -> 71%, things found
  57% -> 47%, invented objects 527 -> 200 (all 300 photos: 54% -> 68%,
  56% -> 48%, invented 1083 -> 502, doubled boxes 86 -> 21), and the
  tuning half agrees; a
  per-class floor table fitted on one half did worse on the other, and a
  minimum box height separated nothing. Client: the Voice and Camera
  buttons are gone at the user's request; the glasses webcam is chosen
  every time and the voice stays on Daniel (`?voice=` overrides). Live
  frames: one in flight at a time, paused during scans, every 300 ms on
  a CPU (the loop alone took 80% of this laptop at 150 ms); boxes live
  2.5x the server's answer gap. Every scan ends with the walkway: blocked
  by what and how far, or clear (hedged) and what it leads to; `wall` is
  in the vocabulary with no height, spoken by direction only, never
  listed or boxed, 0/24 invented on the blank textures. Reading a scan's
  signs while detecting was measured (2.28 -> 2.53 s here) and reverted.
  Ask can go online: `VISION_PROVIDER=nvidia` with `NVIDIA_API_KEY` in
  `.env` builds `NvidiaVisionProvider` (`backend/ai/nvidia_provider.py`):
  questions go to NVIDIA's OpenAI-style endpoint with the frame as an
  `image_url` part (their inline `<img>` tag was never seen by the model
  and it invented a person on a chair; the part form described the same
  kitchen correctly, 1.4 to 2.5 s), scans and reads stay on-device, a
  medicine question never goes online, `verify()` reports the key and
  the model in `/health`. `meta/llama-3.2-11b-vision-instruct` is the
  default; the 90b timed out at 60 s and Gemma, Phi and NeVA are not
  enabled for this key. In the two-engine mesh the thorough engine now
  wins where both read a place (2751b63, for Conrad's receipts drop
  66% -> 55%; his SROIE run decides). The medium detector (`yolov8m-world.pt`) was
  measured on this CPU at floor 0.30 and not adopted: found 43% vs the
  small model's 51%, claims real 67% vs 65%, invented 471 vs 631, 6 of 24
  blank textures got a "wall", 800 ms a 640 px frame vs ~250. Later the
  same evening: detector floor back to 0.30, scan frames match a moved
  camera, scans say the place (hedged "this may be" at 2.5 of the 3.0 bar),
  then people, then things; `SEE_AS=refrigerator:trash can` on this laptop
  only. Suite 516 passed, 3 skipped. `HAZARDS_ENABLED` is still false and
  the depth pass is gated on it.
- **2026-09-19, evening, visionOS-2:** merged 81b0add (lexicon, 38 bundled
  fonts; their `eval/fonts.py` supersedes ours, helper renamed `typefaces.py`);
  RapidOCR detects at 1280 px and recognizes on full-res crops (657 ms/frame,
  spaces intact); Ask is a tap toggle; start screen shows the phone address.
  RapidOCR, Windows, bundled font benchmark n=76: everyday_sans 89%,
  everyday_serif 100%, cursive_script 68% (Vision 4-11%), handwriting 83%,
  novelty 80% exact. Signage n=60: CER 0.028, exact 90%, silent 0,
  hallucinated 1/8 ("AN"; was 0/8 at full-resolution detection; a RapidOCR
  confidence floor should remove it). Packaging: name present 12/24; symbols
  1/12.
- **2026-09-19, later, visionOS-2:** merged fa27618, a3537c5 and 2362b03 from
  `main-project` (all ported onto `TextReader`); RapidOCR reads 13 s -> 2.3 s
  live; digit-in-word fix-up; five-button client restored for parity with the
  Mac; `scene/inference.py` (room guess, hedged unconfirmed objects, landmark
  reminders) in on-device scans; bundled display fonts under `eval/fonts/`
  with `typefaces.py` (`--fonts bundled` compares across machines);
  `run_packaging_eval.py`, `run_font_eval.py`. RapidOCR signage, Windows fonts,
  n=60: CER 0.022, exact 90%, silent 0, hallucinated 0/8 at full resolution.
  The thread on PR #2 has the running numbers.
- **2026-09-19, visionOS-2:** merged `main-project` at 65d5953 (tiling gate,
  position dedupe, plausibility ordering, `run_ocr` thread) under a
  `TextReader` base with `AppleVisionOCR` and `RapidOCR` engines; added
  `run.py`, the start-screen mode notice, the quiet-reconnect fix, and
  platform fonts for `eval/`. Suite 221 passed, 2 skipped. RapidOCR eval on
  Windows: see the PR #2 thread for the current numbers.
