/**
 * Client wiring: camera, voice, spatial audio, speech, haptics.
 */

import { Camera } from "./camera";
import { SpatialAudio } from "./audio/spatial";
import { SpeechPriority, TtsPlayer } from "./audio/tts-player";
import { onVoicesReady, pickVoice, rankVoices, saveVoiceName } from "./audio/voices";
import { Voice } from "./voice";
import { type Boxed, Connection, type ServerEvent } from "./ws";

const DISCLAIMER =
  "VisionOS ready. Tap anywhere to scan, tap Ask to speak. This is an " +
  "assistive tool, not a replacement for your cane or guide dog. Distances " +
  "are estimates.";

// Live frames go to the detector at this rate. On a GPU it is idle most of
// the time; on a CPU-only backend a frame every 700 ms keeps the cores busy
// and starves the read that the user is actually waiting on, so the rate
// drops once the backend says where it runs.
const FRAME_INTERVAL_MS = 700;
// Detection is ~30 ms on this CPU; the old 1500 ms was set when the depth
// pass still ran behind every frame, and it left the boxes a second and a
// half behind the picture.
const FRAME_INTERVAL_CPU_MS = 500;
let frameIntervalMs = FRAME_INTERVAL_MS;
// A detailed frame for the background reader every few seconds, so Read
// answers from what is already known. Rarer on a CPU, where each costs
// most of a second of the reader's time.
const PEEK_INTERVAL_MS = 2500;
const PEEK_INTERVAL_CPU_MS = 4000;
let peekIntervalMs = PEEK_INTERVAL_MS;

// "take me to the door" should start a beacon, not narrate. Checked before
// the question is sent, because guidance is a different mode from an answer.
const BEACON_PHRASES = [
  /^(?:take|guide|lead|walk) me to (?:the |a |an )?(.+)$/i,
  /^(?:navigate|go) to (?:the |a |an )?(.+)$/i,
  /^(?:find|locate) (?:the |a |an )?(.+?)(?: for me)?$/i,
];
const STOP_PHRASES = /^(?:stop|cancel|quiet|never mind|nevermind)\b/i;

const el = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

const video = el<HTMLVideoElement>("camera");
const readArea = el<HTMLDivElement>("read-area");
const boxes = el<HTMLCanvasElement>("boxes");
const startButton = el<HTMLButtonElement>("start");
const tapLayer = el<HTMLButtonElement>("tap-layer");
const controls = el<HTMLDivElement>("controls");
const askButton = el<HTMLButtonElement>("ask");
const status = el<HTMLParagraphElement>("status");
const transcript = el<HTMLDivElement>("transcript");
const modeLine = el<HTMLElement>("mode");

const tts = new TtsPlayer();
const spatial = new SpatialAudio();
const camera = new Camera(video);

// Spoken announcements are once-per-session. Reconnects re-fire the events
// that trigger them, and a reconnect loop turns that into an endless monologue.
let announcedMode = false;
let announcedDisconnect = false;
let starting = false;
let started = false;

// While true the fast frame loop is suspended. On a CPU-only backend the
// object detector and OCR compete for the same cores, and a read that takes
// seconds should not also be fighting a frame of YOLO every 700 ms. Cleared
// by the read's trace event, with a backstop in case none arrives.
let readPending = false;
const READ_TIMEOUT_MS = 20000;
// Enough frames for majority agreement; the gap lets the scene shift slightly
// so blur and glare differ between them, which is what makes the vote useful.
const READ_BURST_FRAMES = 3;
const READ_BURST_GAP_MS = 120;

let voiceIndex = 0;
const VOICE_SAMPLE =
  "This is how I'll sound. A doorway is about three meters ahead, at your two o'clock.";

const setStatus = (text: string): void => {
  status.textContent = text;
};

/** Surface a failure the user cannot see. Silence reads as a freeze. */
function reportFailure(message: string): void {
  setStatus(message);
  show(message);
  tts.say(message, SpeechPriority.Answer);
}

// Without this, any uncaught error during startup leaves the start screen up
// with no explanation -- which is exactly how it presented.
window.addEventListener("error", (event) => {
  console.error(event.error ?? event.message);
  setStatus(`Error: ${event.message}`);
});
window.addEventListener("unhandledrejection", (event) => {
  console.error(event.reason);
  setStatus(`Error: ${String(event.reason)}`);
});

function show(text: string, kind: "speech" | "hazard" = "speech"): void {
  const line = document.createElement("p");
  line.textContent = text;
  if (kind === "hazard") line.className = "hazard";
  transcript.prepend(line);
  while (transcript.childElementCount > 8) transcript.lastElementChild?.remove();
}

// What this backend can do, in one sentence, before the user commits to
// starting. The same facts are spoken once connected; showing them here too
// means a sighted helper can see at a glance whether a key is missing.
type Health = { provider_active?: string; ocr?: string; lan_address?: string | null };

function describeMode(health: Health | null): string {
  if (!health) return "Backend not reachable yet. It will keep trying once you start.";
  const ocr =
    health.ocr && health.ocr !== "none"
      ? `Reads text on-device (${health.ocr}).`
      : "No on-device text reader.";
  switch (health.provider_active) {
    case "ClaudeVisionProvider":
      return `Full mode: Claude describes the scene and answers questions. ${ocr}`;
    case "ReplayVisionProvider":
      return `Replay mode: scripted descriptions that ignore the camera. ${ocr}`;
    case "LocalSceneProvider":
      return `On-device mode: describes recognized objects and reads text, no open questions. ${ocr} Add an Anthropic key to the backend .env for full descriptions.`;
    default:
      return ocr;
  }
}

async function showMode(): Promise<void> {
  try {
    const response = await fetch("/health", { cache: "no-store" });
    const health = response.ok ? ((await response.json()) as Health) : null;
    modeLine.textContent = describeMode(health);
    // On the computer that runs the servers, say where a phone should go.
    // The phone needs the HTTPS port; the address changes with the network.
    const local = location.hostname === "localhost" || location.hostname === "127.0.0.1";
    if (health?.lan_address && local) {
      modeLine.textContent += ` Phone on the same Wi-Fi: https://${health.lan_address}:5173`;
    }
  } catch {
    modeLine.textContent = describeMode(null);
  }
}

const socketUrl = (): string => {
  const override = new URLSearchParams(location.search).get("backend");
  if (override) return override;
  // Same origin: Vite proxies /ws. Connecting to :8000 directly would need a
  // second accepted certificate, which Safari never prompts for on a socket.
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
};

function onServerEvent(event: ServerEvent): void {
  switch (event.type) {
    case "ready": {
      const modes: Record<string, { label: string; spoken?: string }> = {
        ReplayVisionProvider: {
          label: "REPLAY — scripted, ignores camera",
          spoken: "Replay mode. Descriptions are scripted and do not match your camera.",
        },
        LocalSceneProvider: {
          label: "On-device",
          spoken:
            "Running on device. I can describe what I recognize and read text, " +
            "but I can't answer open questions.",
        },
      };
      const mode = modes[event.provider_active];
      setStatus(mode?.label ?? "Connected");
      frameIntervalMs = event.device === "cpu" ? FRAME_INTERVAL_CPU_MS : FRAME_INTERVAL_MS;
      peekIntervalMs = event.device === "cpu" ? PEEK_INTERVAL_CPU_MS : PEEK_INTERVAL_MS;

      // Spoken once per session, not per `ready`. The server sends `ready` on
      // every connect, so a reconnect loop repeated this announcement forever.
      if (mode?.spoken && !announcedMode) {
        announcedMode = true;
        tts.say(mode.spoken, SpeechPriority.Answer);
      }
      break;
    }

    case "speech":
      tts.say(event.text, SpeechPriority.Answer);
      show(event.text);
      break;

    case "hazard": {
      // Earcon first: it lands in ~20 ms and arrives from the right
      // direction, while the sentence takes about a second to say.
      spatial.play("hazard", event.azimuth_deg, event.distance_m || 1);
      navigator.vibrate?.(event.severity >= 2 ? [120, 50, 120] : [80]);
      tts.say(event.text, SpeechPriority.Hazard);
      show(event.text, "hazard");
      break;
    }

    case "beacon":
      if (spatial.beaconActive) {
        spatial.updateBeacon(event.azimuth_deg, event.distance_m);
      } else {
        spatial.startBeacon(event.azimuth_deg, event.distance_m);
      }
      break;

    case "beacon_stop":
      spatial.stopBeacon();
      break;

    case "inventory": {
      // Everything the scan saw, with confidence: on screen always, spoken
      // only when asked for with ?verbose=1, since it is a list, not a picture.
      show(event.text);
      if (verbose) tts.say(event.text, SpeechPriority.Answer);
      const boxed = event.items.filter((item) => item.box !== null) as Boxed[];
      if (boxed.length > 0) {
        scanBoxesUntil = performance.now() + SCAN_BOXES_MS;
        drawBoxes(boxed);
      }
      break;
    }

    case "detections":
      // Live boxes, unless a scan's are still on show.
      if (performance.now() >= scanBoxesUntil) drawBoxes(event.items);
      break;

    case "trace":
      // The read is done with the hi-res frame; let the fast loop resume.
      if (event.label.startsWith("read")) readPending = false;
      console.info(
        `[latency] ${event.label} first_word=${event.stages.first_sentence ?? "-"}ms total=${event.total_ms}ms`
      );
      break;
  }
}

const connection = new Connection(socketUrl(), {
  onEvent: onServerEvent,
  onConnectionChange: (connected) => {
    if (connected) {
      announcedDisconnect = false;
      setStatus("Connected");
      return;
    }
    setStatus("Reconnecting…");
    // Said once per outage, not once per retry.
    if (!announcedDisconnect) {
      announcedDisconnect = true;
      tts.say("I lost connection. Reconnecting.", SpeechPriority.Answer);
    }
  },
});

const voice = new Voice({
  onTranscript: (text) => {
    show(`“${text}”`);
    routeSpokenCommand(text);
  },
  onStateChange: (listening) => {
    askButton.dataset.listening = String(listening);
    if (listening) {
      // Stop talking before listening, or the mic hears the assistant.
      tts.stopAll();
      spatial.play("info", 0, 1);
      setStatus("Listening… tap Ask again to stop");
    } else {
      setStatus("Connected");
    }
  },
  onError: (message) => {
    setStatus(message);
    tts.say(message, SpeechPriority.Answer);
  },
});

function routeSpokenCommand(text: string): void {
  const trimmed = text.trim();

  if (STOP_PHRASES.test(trimmed)) {
    tts.stopAll();
    spatial.stopBeacon();
    connection.sendIntent("stop_beacon");
    return;
  }

  for (const pattern of BEACON_PHRASES) {
    const match = trimmed.match(pattern);
    if (match?.[1]) {
      connection.sendIntent("locate", match[1].replace(/[?.!]$/, ""));
      return;
    }
  }

  connection.sendIntent("ask", trimmed);
}

async function begin(): Promise<void> {
  // Repeated taps on an apparently-stuck screen would otherwise re-run the
  // whole sequence and stack up duplicate disclaimers and frame loops.
  if (starting || started) return;
  starting = true;

  try {
    // Both must happen inside the tap handler: iOS starts audio suspended and
    // refuses speech synthesis until a gesture has occurred. Neither throws.
    tts.prime();
    spatial.init();

    try {
      // ?camera=<part of its name> pins a camera, for a webcam worn on
      // glasses beside a laptop's own.
      await camera.start(new URLSearchParams(location.search).get("camera"));
    } catch (err) {
      reportFailure((err as Error).message);
      return;
    }

    started = true;
    startButton.hidden = true;
    placeReadArea();
    tapLayer.hidden = false;
    controls.hidden = false;
    setStatus("Connecting…");
    tts.say(DISCLAIMER, SpeechPriority.Answer);
    // Which camera is in use, and whether it can focus, are states the user
    // cannot see: a webcam on a pair of glasses and the one above the laptop
    // screen are indistinguishable from the inside, and a lens that cannot
    // focus up close changes how a label has to be held.
    tts.say(`Using ${camera.label}, ${camera.focus}.`, SpeechPriority.Answer);
    connection.connect();

    let lastFrameAt = 0;
    let lastPeekAt = 0;
    setInterval(async () => {
      if (!camera.isRunning || !connection.isOpen || readPending) return;
      const now = performance.now();
      if (now - lastPeekAt >= peekIntervalMs) {
        lastPeekAt = now;
        const peek = await camera.capturePeek();
        if (peek && !readPending) connection.sendPeekFrame(peek);
        return;
      }
      if (now - lastFrameAt < frameIntervalMs) return;
      lastFrameAt = now;
      const frame = await camera.captureFast();
      // Re-checked after the await: a read may have started while capturing.
      if (frame && !readPending) connection.sendFrame(frame);
    }, 100);
  } catch (err) {
    // Anything unexpected must still surface. A silent throw here is
    // indistinguishable from a frozen app to someone who cannot see it.
    reportFailure(`Couldn't start: ${(err as Error).message}`);
  } finally {
    starting = false;
  }
}

const SCAN_FRAMES = 2;
const SCAN_GAP_MS = 150;
// ?verbose=1 also speaks the full inventory with confidences after a scan.
const verbose = new URLSearchParams(location.search).get("verbose") === "1";

/**
 * A scan is two frames of the whole view at scan size, so the detector
 * can see what the live loop's small frames cannot, and only what both
 * frames agree on is spoken as seen. Without a running camera it falls
 * back to describing what the live loop already knows.
 */
async function scanScene(): Promise<void> {
  if (!connection.isOpen) {
    reportFailure("Not connected yet.");
    return;
  }
  if (!camera.isRunning) {
    connection.sendIntent("scan");
    return;
  }
  spatial.play("info", 0, 1);
  setStatus("Scanning…");
  const captured: Blob[] = [];
  for (let i = 0; i < SCAN_FRAMES; i++) {
    const frame = await camera.captureScan();
    if (frame) captured.push(frame);
    if (i < SCAN_FRAMES - 1) await new Promise((resolve) => setTimeout(resolve, SCAN_GAP_MS));
  }
  if (captured.length === 0) {
    connection.sendIntent("scan");
    return;
  }
  connection.sendScanFrames(captured);
}

// How long a scan's boxes stay up before the live ones take over again.
let scanBoxesUntil = 0;
const SCAN_BOXES_MS = 4000;
// A box eases from where it was to where it is next over roughly the time
// until the next update, and appears or disappears over a fade rather than
// popping, so the overlay moves with the picture instead of stepping.
const BOX_FADE_MS = 300;
const BOX_MATCH_DISTANCE = 0.25;

type Box = [number, number, number, number];
type BoxTrack = {
  label: string;
  confidence: number;
  from: Box;
  to: Box;
  movedAt: number;
  bornAt: number;
  fadingSince: number | null;
};
let boxTracks: BoxTrack[] = [];
let boxFrame: number | null = null;

function easeOut(t: number): number {
  return 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);
}

function boxAt(track: BoxTrack, now: number): Box {
  const t = easeOut((now - track.movedAt) / Math.max(120, frameIntervalMs));
  return track.from.map((v, i) => v + (track.to[i] - v) * t) as Box;
}

function boxCenterDistance(a: Box, b: Box): number {
  const ax = (a[0] + a[2]) / 2, ay = (a[1] + a[3]) / 2;
  const bx = (b[0] + b[2]) / 2, by = (b[1] + b[3]) / 2;
  return Math.hypot(ax - bx, ay - by);
}

/**
 * A red outline and label around everything the detector believes it
 * sees, for a sighted helper checking the glasses. Each new set of boxes is
 * matched to the boxes already on screen by label and nearness; matched
 * ones glide, new ones fade in, lost ones fade out.
 */
function drawBoxes(items: Boxed[]): void {
  const now = performance.now();
  const unmatched = boxTracks.filter((t) => t.fadingSince === null);
  const next: BoxTrack[] = boxTracks.filter((t) => t.fadingSince !== null);
  for (const item of items) {
    let best: BoxTrack | null = null;
    let bestDistance = BOX_MATCH_DISTANCE;
    for (const track of unmatched) {
      if (track.label !== item.label) continue;
      const distance = boxCenterDistance(boxAt(track, now), item.box);
      if (distance < bestDistance) {
        best = track;
        bestDistance = distance;
      }
    }
    if (best) {
      unmatched.splice(unmatched.indexOf(best), 1);
      next.push({ ...best, confidence: item.confidence, from: boxAt(best, now), to: item.box, movedAt: now });
    } else {
      next.push({ label: item.label, confidence: item.confidence, from: item.box, to: item.box, movedAt: now, bornAt: now, fadingSince: null });
    }
  }
  for (const track of unmatched) {
    const here = boxAt(track, now);
    next.push({ ...track, from: here, to: here, movedAt: now, fadingSince: now });
  }
  boxTracks = next;
  if (boxFrame === null) boxFrame = requestAnimationFrame(renderBoxes);
}

function renderBoxes(): void {
  boxFrame = null;
  const now = performance.now();
  const frame = camera.frameOnScreen();
  const scale = window.devicePixelRatio || 1;
  boxes.width = Math.round(window.innerWidth * scale);
  boxes.height = Math.round(window.innerHeight * scale);
  const ctx = boxes.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  boxTracks = boxTracks.filter((t) => t.fadingSince === null || now - t.fadingSince < BOX_FADE_MS);
  if (!frame) return;
  ctx.lineWidth = 2;
  ctx.font = "13px system-ui, sans-serif";
  let animating = false;
  for (const track of boxTracks) {
    const fadeIn = Math.min(1, (now - track.bornAt) / BOX_FADE_MS);
    const fadeOut = track.fadingSince === null ? 1 : 1 - (now - track.fadingSince) / BOX_FADE_MS;
    const alpha = Math.max(0, Math.min(fadeIn, fadeOut));
    const moving = now - track.movedAt < Math.max(120, frameIntervalMs);
    if (moving || fadeIn < 1 || track.fadingSince !== null) animating = true;
    const [x1, y1, x2, y2] = boxAt(track, now);
    const left = frame.left + x1 * frame.width;
    const top = frame.top + y1 * frame.height;
    const width = (x2 - x1) * frame.width;
    const height = (y2 - y1) * frame.height;
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = "#e53935";
    ctx.strokeRect(left, top, width, height);
    const text = `${track.label} ${Math.round(track.confidence * 100)}%`;
    const pad = 4;
    const textWidth = ctx.measureText(text).width;
    const tagTop = top >= 18 ? top - 18 : top;
    ctx.fillStyle = "#e53935";
    ctx.fillRect(left, tagTop, textWidth + pad * 2, 18);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, left + pad, tagTop + 13);
  }
  ctx.globalAlpha = 1;
  if (animating) boxFrame = requestAnimationFrame(renderBoxes);
}
window.addEventListener("resize", () => {
  if (boxFrame === null) boxFrame = requestAnimationFrame(renderBoxes);
});

/** Draw the read area over the preview, wherever the frame landed on screen. */
function placeReadArea(): void {
  const rect = camera.readWindowOnScreen();
  readArea.hidden = rect === null;
  if (!rect) return;
  readArea.style.left = `${rect.left}px`;
  readArea.style.top = `${rect.top}px`;
  readArea.style.width = `${rect.width}px`;
  readArea.style.height = `${rect.height}px`;
}
video.addEventListener("loadedmetadata", placeReadArea);
video.addEventListener("resize", placeReadArea);
window.addEventListener("resize", placeReadArea);

/** The next camera, spoken by name, and remembered for next time. */
async function switchCamera(): Promise<void> {
  if (!started) return;
  try {
    const label = await camera.next();
    setStatus(`Camera: ${label}, ${camera.focus}`);
    tts.say(`Using ${label}, ${camera.focus}.`, SpeechPriority.Answer);
  } catch (err) {
    reportFailure((err as Error).message);
  }
}

async function readText(): Promise<void> {
  if (!connection.isOpen) {
    reportFailure("Not connected yet.");
    return;
  }
  // Audible acknowledgement: reading takes a moment and the tap is otherwise
  // silent, which reads as a missed tap.
  spatial.play("info", 0, 1);
  setStatus("Reading…");
  // The frame loop is suspended for the whole operation.
  readPending = true;
  try {
    // A burst, not one frame. Hand-held capture blurs and glares differently
    // each time, and the server keeps only text that several frames agree on
    // -- which is what separates real text from OCR noise.
    // Let an autofocus lens settle on the label first; a burst captured
    // mid-hunt is three soft frames that agree on nothing.
    await camera.refocus();
    // Turn the exposure down for the burst: a glossy label blows out to
    // white under automatic exposure, and the ink is what the reader
    // needs. Put back straight after, so the preview and scans are as
    // they were.
    await camera.dimForRead();
    const captured: Blob[] = [];
    try {
      for (let i = 0; i < READ_BURST_FRAMES; i++) {
        const frame = await camera.captureDetailed();
        if (frame) captured.push(frame);
        if (i < READ_BURST_FRAMES - 1) {
          await new Promise((resolve) => setTimeout(resolve, READ_BURST_GAP_MS));
        }
      }
    } finally {
      await camera.restoreExposure();
    }

    if (captured.length === 0) {
      reportFailure("Couldn't capture the image to read.");
      return;
    }

    // One tagged message; the server reads it directly and it never enters
    // the perception pipeline. See sendReadFrames() in ws.ts.
    connection.sendReadFrames(captured);
  } finally {
    // Cleared on the trace event; this is the backstop if none arrives.
    window.setTimeout(() => {
      readPending = false;
    }, READ_TIMEOUT_MS);
  }
}

/**
 * Voices load asynchronously; reading getVoices() once at startup usually
 * returns an empty list.
 */
function applyBestVoice(): void {
  const override = new URLSearchParams(location.search).get("voice");
  const chosen = pickVoice(override);
  if (!chosen) return;

  tts.setVoice(chosen);
  voiceIndex = Math.max(
    0,
    rankVoices().findIndex((entry) => entry.voice.name === chosen.name)
  );
  console.info(
    "[voices] using %s — available: %s",
    chosen.name,
    rankVoices().map((entry) => `${entry.voice.name} (${entry.score})`).join(", ")
  );
}

/** Cycle to the next-best voice and speak a sample so it can be judged. */
function cycleVoice(): void {
  const ranked = rankVoices();
  if (ranked.length === 0) {
    reportFailure("No speech voices are available in this browser.");
    return;
  }

  voiceIndex = (voiceIndex + 1) % ranked.length;
  const chosen = ranked[voiceIndex].voice;

  tts.setVoice(chosen);
  saveVoiceName(chosen.name);
  tts.stopAll();
  setStatus(`Voice: ${chosen.name}`);
  tts.say(`${chosen.name}. ${VOICE_SAMPLE}`, SpeechPriority.Answer);
}

onVoicesReady(applyBestVoice);
void showMode();

// One action per control, so a tap on the screen, a key press and a watch
// button wired through an Arduino all do exactly the same thing.
const actions = {
  scan: () => void scanScene(),
  read: () => void readText(),
  // Tap to toggle, not push-to-talk. A held button fails on phones: the
  // browser cancels the press the moment it takes it for a scroll or a
  // long-press, so recognition stopped before the user had said a word.
  // Recognition ends itself after one utterance; a second tap ends it early.
  ask: () => (voice.isListening ? voice.stop() : voice.start()),
  voice: () => cycleVoice(),
  switch: () => void switchCamera(),
  stop: () => {
    tts.stopAll();
    spatial.stopBeacon();
    connection.sendIntent("stop_beacon");
  },
};
type Action = keyof typeof actions;

startButton.addEventListener("click", begin);
tapLayer.addEventListener("click", actions.scan);
for (const name of Object.keys(actions) as Action[]) {
  el(name).addEventListener("click", (e) => {
    e.stopPropagation();
    actions[name]();
  });
}

// Keys drive the same actions, so a watch whose Arduino presents itself as
// a USB keyboard runs the app with nothing in between. Digits suit a
// keypad; the letters suit a person at a keyboard. Before the app has
// started, any mapped key starts it: a key press is the gesture the
// browser needs before it will speak.
const KEYS: Record<string, Action> = {
  "1": "scan", s: "scan", " ": "scan",
  "2": "read", r: "read",
  "3": "ask", a: "ask",
  "4": "voice", v: "voice",
  "5": "switch", c: "switch",
  "0": "stop", x: "stop", Escape: "stop",
};
window.addEventListener("keydown", (event) => {
  if (event.repeat || event.altKey || event.ctrlKey || event.metaKey) return;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (!started) {
    if (key in KEYS || key === "Enter") {
      event.preventDefault();
      void begin();
    }
    return;
  }
  const action = KEYS[key];
  if (!action) return;
  event.preventDefault();
  actions[action]();
});

// The watch's Arduino can instead talk over its USB serial port, one command
// per line: SCAN, READ, ASK, VOICE or STOP. That needs no keyboard emulation,
// so any board will do, but the browser only opens a port a person has
// picked, hence the Watch button. Shown only where Web Serial exists.
type SerialPortLike = {
  open(options: { baudRate: number }): Promise<void>;
  readable: ReadableStream<BufferSource> | null;
};
const serial = (navigator as unknown as { serial?: { requestPort(): Promise<SerialPortLike> } })
  .serial;
const watchButton = document.getElementById("watch") as HTMLButtonElement | null;
if (watchButton && serial) {
  watchButton.hidden = false;
  watchButton.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      const port = await serial.requestPort();
      await port.open({ baudRate: 9600 });
      setStatus("Watch connected");
      tts.say("Watch connected.", SpeechPriority.Answer);
      void listenToWatch(port);
    } catch (err) {
      reportFailure(`Couldn't connect the watch: ${(err as Error).message}`);
    }
  });
}

async function listenToWatch(port: SerialPortLike): Promise<void> {
  if (!port.readable) return;
  const reader = port.readable.pipeThrough(new TextDecoderStream()).getReader();
  let buffered = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffered += value;
      let newline = buffered.indexOf("\n");
      while (newline >= 0) {
        const line = buffered.slice(0, newline).trim().toLowerCase();
        buffered = buffered.slice(newline + 1);
        if (line in actions) {
          if (started) actions[line as Action]();
          else void begin();
        }
        newline = buffered.indexOf("\n");
      }
    }
  } catch {
    reportFailure("Lost the connection to the watch.");
  }
}

if (!tts.isSupported) {
  setStatus("This browser has no speech synthesis. Try Safari or Chrome.");
}
if (!voice.isSupported) {
  askButton.hidden = true;
}
