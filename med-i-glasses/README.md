# Med-i-Glasses — a digital sense of sight

A webcam on a pair of glasses (or a phone) feeds a scene model. The wearer
hears what is around them and where, reads any sign or label, asks questions
by voice, is told when they are back in a place they have been, and is warned
when a label names something they are allergic to, with an email to their
doctor. Nothing visual is load-bearing: every state reaches the wearer as
speech, a tone or a vibration. The rule behind most decisions: speaking
something wrong is worse than saying nothing.

## Run it

Python 3.10 to 3.12 and Node LTS installed, then:

```
cd med-i-glasses
python run.py
```

The first run creates `.venv`, installs the backend and client dependencies
(a few hundred MB, torch among them), downloads the model weights (about
450 MB), copies `.env.example` to `.env`, and asks whether you will use it
from a phone on this Wi-Fi (HTTPS on port 5173; accept the certificate
warning once) or a browser on this computer (HTTP on `localhost:5174`).
Every run reports which vision provider and text reader this machine can
use, then starts the backend (port 8000) and the client together.

```
python run.py --yes      take the defaults, ask nothing
python run.py --local    browser on this computer
python run.py --phone    phone on the LAN
python run.py --check    report what would happen and exit
```

On macOS with the tools on PATH, `make setup && make precache && make doctor
&& make dev` does the same by hand; `make test` runs the suite. On Windows
keep the checkout at a short path (`C:\Users\<you>\vthacks-2026`), since long
paths break Python imports; `run.py` finds Node even when the installer left
it off PATH.

On a Mac: a `.venv` with Python 3.12 and `pip install -r requirements.txt`;
Apple Vision needs no install and RapidOCR pulls its own weights on the first
read; perception runs on MPS, and the detector weights (26 MB) download on the
first start, so it needs the network once and takes about 35 s, then about
6 s. The watch is `/dev/cu.usbmodem*`; glob it, the name changes across
resets, and the port is exclusive: the Arduino IDE's serial monitor keeps it
after its window closes, and the app then reads nothing with no error. With
`ELEVENLABS_API_KEY` set, run `eval/warm_voice.py` once after any change to
a spoken phrase.

Open the address `run.py` prints, press Start (or any key), and the app says
it is ready.

## Keys, all optional, in `med-i-glasses/.env`

`.env` is never committed. Without any key the app still describes what it
recognizes, reads text, remembers places and runs the allergy scanner; keys
add the parts that need a network.

| Lines in `.env` | What they turn on |
| --- | --- |
| `VISION_PROVIDER=nvidia` and `NVIDIA_API_KEY=...` (build.nvidia.com) | Ask answers come from a hosted vision model; scans and reads stay on-device. A medicine question never goes online. `ANTHROPIC_API_KEY=...` with `VISION_PROVIDER=claude` is the other option. |
| `ALERT_SMTP_USER=you@gmail.com` and `ALERT_SMTP_PASSWORD=...` (a Gmail app password) | The allergy alert is emailed to the doctor's address in the profile. Without them every alert is written to `data/outbox/` and the wearer hears that mail is not set up. |
| `ELEVENLABS_API_KEY=...` | A better voice for answers and reads, synthesized once and cached to disk. Without it the browser's voice speaks everything. Warnings always use the browser's voice, which needs no network. |
| `SEE_AS=refrigerator:trash can` | Sees one detector label as another, for a venue where tall bins read as refrigerators. Empty by default. |
| `OCR_ENGINE=rapidocr` | Pins the text reader; `auto` takes Apple Vision on a Mac, then RapidOCR anywhere. |

## Using it

| Control | Key | What happens |
| --- | --- | --- |
| tap anywhere, or Scan | 1 | Describes the room: the place if it is one you have been in, then people and what they are doing, then things by direction and distance, then whether the way ahead is clear. |
| Read | 2 | Reads any sign or label in view from a burst of frames and speaks what the frames agree on. A medicine label's dose is spoken only when frames agree on it. |
| Ask | 3 | Listens. Say "Jarvis" and then the question ("Jarvis, is there a cup on the table", "Jarvis, where am I", "Jarvis, take me to the door"). Tap again to stop listening. |
| Stop | 0 | Silences speech and any beacon. Stop words never need the name. |
| Places | P | A sheet for a sighted helper: every remembered place with its views; rename, delete, link. Every edit is spoken. |
| Blueprint | B | The last scan drawn from above: unobstructed and obstructed cells, the walkway, walls. |
| Allergies | L | The wearer's allergies, name and doctor's email; a test-email button. Every edit is spoken. |
| Watch | | Connects a USB watch of buttons over Web Serial (Chrome, Edge). One line per press: SCAN, READ or ASK at 115200 baud; see `hardware/README.md`. |

The keys also drive the app from a keyboard, and a watch that presents as a
keyboard works with nothing in between.

## The food allergy scanner

The profile (`data/profile.json`, edited in the Allergies sheet) lists what
the wearer is allergic to. While the app runs it reads what is in view in
the background, and on every read it looks for evidence, strongest first:

1. **A barcode on the packet**, looked up in Open Food Facts. A database
   cannot invent an allergen. A hit on the product's allergens sounds the
   warning and emails the doctor; a "traces" entry only warns.
2. **A CONTAINS, ALLERGENS or INGREDIENTS line** the reader was at least
   80% sure of in two frames, naming an allergen as a whole word. "Dairy
   free", "no nuts" and "does not contain milk" are absences; almond milk
   is not dairy; "may contain" is a spoken warning, not an email.
3. **The online model's opinion** over the label's text and picture, as a
   fixed verdict (confirm, deny, or check before eating) that must say what
   it saw. It only ever speaks.

A food the camera recognizes is a trigger, never evidence: "It looks like
you are eating; hold the label up and press Read." The email carries the
evidence, the place, the time and a photo of the moment; one per allergen
per ten minutes; "Jarvis, false alarm" sends a correction. Measured on
Conrad's 40-label corpus the matcher invented nothing and mishandled no
negation; the reader is the limit on small print (7 statement lines
recovered of 40), which is why the barcode step comes first.

## Reading text

Two engines share one pipeline: Apple Vision on macOS (15 ms a frame) and
RapidOCR on any CPU (about 650 ms). On a Mac both load and the fast one
answers when it is sure. A read is a burst of three full-resolution frames;
the server keeps the lines the frames agree on, drops what does not look like
language, and speaks the rest in reading order. Read frames never enter the
perception pipeline.

## Measuring it

```
.venv\Scripts\python -m pytest backend/tests -q        # Windows; .venv/bin/python elsewhere
PYTHONPATH=. .venv\Scripts\python eval\run_ocr_eval.py 60
```

`eval/` renders its own corpora (signage in 16 system fonts and 38 bundled
ones, packaging, prescription labels, allergen statements, receipts) and
simulates a hand-held capture, so a change to reading is scored before and
after; `eval/run_detect_eval.py` scores the detector on blank textures and
on 300 COCO photos. Two evals need a download first: `run_detect_eval.py`
wants `eval/fetch_everyday.py` (about 95 MB of COCO photos) and
`run_sroie_eval.py` wants the SROIE receipts; the rest render their own
corpora. The current numbers and how they were reached are in the log at
the end of `../CLAUDE.md`. Any change to reading is measured; "does the
output contain the word" is not a test.

## Layout

```
backend/
  main.py            WebSocket protocol and per-session logic
  config.py          every setting, env-overridable, documented
  ai/ocr.py          the reading pipeline and both engines
  ai/text_quality.py telling real text from OCR noise
  ai/lexicon.py      near-miss correction against real-world wording
  ai/medication.py   the dose guard
  ai/nvidia_provider.py, ai/vision.py, ai/local_provider.py   who answers Ask
  alerts/            the allergy scanner: profile, matcher, eating trigger,
                     barcode lookup, online second opinion, email agent, watch
  perception/        detector (YOLO-World, 140 classes), depth, tracker, geometry
  scene/             the scene model, the scan description, the place memory
  hazards/           deterministic obstacle alerts (no model on this path)
  speech/            sentence chunking, phrasing, the cached voice
  places_api.py, profile_api.py   the sheets' HTTP routes
client/src/
  main.ts            interaction model, keys, the watch
  camera.ts          the glasses webcam, capture, exposure
  places.ts, blueprint.ts, allergies.ts, sheet.ts   the helper sheets
  audio/             earcons, the speech queue, voices
eval/                corpora and scoring scripts
hardware/            the watch sketch and its protocol
data/                this machine's memory: places, profile, outbox (gitignored)
```
