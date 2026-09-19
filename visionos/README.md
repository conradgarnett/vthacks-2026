# VisionOS — a digital sense of sight

Point a phone at the room and hear what is there: objects with direction and
distance, obstacles before you reach them, and any sign or label in view.

```
make setup      # venv + backend + client deps
make precache   # model weights, before demo day
make doctor     # credentials, weights, OCR engine, ports
make dev        # backend on :8000, client on https://<lan-ip>:5173
make test
```

## Using it

| Action        | What happens                                              |
| ------------- | --------------------------------------------------------- |
| tap anywhere  | describes the room, nearest things first                  |
| Read          | reads any text in view                                    |
| hold Ask      | speak a question, or a command such as "take me to the door", "read the sign", "stop", "next voice" |
| Stop          | silences speech and any active beacon                     |

## Reading text

Reads are answered by on-device OCR first, and only escalate to Claude when
the local engine finds nothing. Two engines are supported and the first one
that loads is used:

- **Apple Vision** on macOS, when `pyobjc-framework-Vision` is installed.
- **RapidOCR** on every platform, on CPU, no credentials.

`OCR_ENGINE` in `.env` pins one, or `none` sends every read to Claude.

A read captures a burst of three full-resolution frames and sends them as one
tagged message. The server reads each frame, keeps the lines the frames agree
on (OCR noise moves between frames; real text does not), drops anything that
does not look like language, and speaks the rest in reading order. The burst
never enters the perception pipeline: live frames for object detection are
small and frequent, and a full-resolution frame among them would break the
tracker's box matching and duplicate every object.

## Layout

```
backend/
  main.py          WebSocket protocol and per-session logic
  ai/ocr.py        OCR engines, consensus, reading order, speech formatting
  ai/text_quality.py     telling real text from OCR noise
  ai/vision.py     Claude and replay providers
  ai/local_provider.py   answers from the scene model with no credentials
  perception/      detector, depth, tracker, geometry
  scene/           egocentric scene model and queries
  hazards/         deterministic obstacle alerts (no LLM on this path)
  speech/          sentence chunking and spoken grammar
client/
  src/main.ts      interaction model
  src/camera.ts    fast and full-resolution capture
  src/audio/       spatial earcons, TTS queue, voice ranking
  judge/           latency dashboard for sighted observers
```
