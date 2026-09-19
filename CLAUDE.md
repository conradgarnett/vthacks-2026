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

## Verifying a change

```bash
cd visionos
.venv/bin/python -m pytest backend/tests -q          # 188 tests
PYTHONPATH=..:. .venv/bin/python eval/run_ocr_eval.py 60
```

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

## Working in parallel

Several branches are active (`main-project`, `visionOS-2`, `side-project`).
Merge via PR; do not push to a branch someone else owns, and do not
force-push. When two versions of the same file disagree, run both through
`eval/` and keep the better score — a measurement rather than an argument.

`HAZARDS_ENABLED` is currently `false` while the detection vocabulary is
tuned; false warnings talk over everything else. The engine is intact and
tested.
