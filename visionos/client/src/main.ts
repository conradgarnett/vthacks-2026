/**
 * Client wiring: camera, voice, spatial audio, speech, haptics.
 */

import { Camera } from "./camera";
import { SpatialAudio } from "./audio/spatial";
import { SpeechPriority, TtsPlayer } from "./audio/tts-player";
import { onVoicesReady, pickVoice, rankVoices } from "./audio/voices";
import { initBlueprint } from "./blueprint";
import { initPlaces } from "./places";
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
// On a CPU the interval is a floor, not a rate: the next live frame goes
// only once the server has answered the last one (a "detections" event),
// so the server is never saturated by live frames, nothing is dropped, and
// a scan or a read that arrives finds the detector free. Measured here,
// detection takes about 270 ms a frame, so this lands at three or four
// frames a second on its own; sending every 150 ms just had every second
// frame dropped and the detector always busy. The floor is 300 ms rather
// than the detector's own 180 ms because each detection takes every core:
// back to back, the live loop alone used 80% of this laptop and a scan or
// a read had to fight it. At 300 ms it uses about 60%, boxes update three
// times a second, and the rest is there for what the user asked for.
const FRAME_INTERVAL_CPU_MS = 300;
// A frame that never gets an answer (dropped behind a scan, say) stops
// blocking the loop after this long.
const FRAME_IN_FLIGHT_MAX_MS = 1200;
let frameIntervalMs = FRAME_INTERVAL_MS;
let frameInFlight = false;
let frameSentAt = 0;
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

// The voice the user chose. There is no button to change it any more: a
// changed voice is a surprise to someone who cannot see why.
const VOICE_NAME = "daniel";

type StatusState = "idle" | "ok" | "busy" | "listening" | "error";

/** The status pill: its text, and the dot or spinner beside it. */
const setStatus = (text: string, state: StatusState = "idle"): void => {
  status.textContent = text;
  status.dataset.state = state;
  if (state === "busy") lastServerMessageAt = performance.now();
};

// The spinner turns while the app works and stops when the server has gone
// quiet for longer than any scan, read or question should take: stuck, not
// slow, and a sighted helper can tell the two apart at a glance.
const STALL_MS = 5000;
let lastServerMessageAt = 0;
window.setInterval(() => {
  const busy = status.dataset.state === "busy";
  status.dataset.stalled = String(busy && performance.now() - lastServerMessageAt > STALL_MS);
}, 250);

/** Surface a failure the user cannot see. Silence reads as a freeze. */
function reportFailure(message: string): void {
  setStatus(message, "error");
  show(message);
  tts.say(message, SpeechPriority.Answer);
}

// Without this, any uncaught error during startup leaves the start screen up
// with no explanation -- which is exactly how it presented.
window.addEventListener("error", (event) => {
  console.error(event.error ?? event.message);
  setStatus(`Error: ${event.message}`, "error");
});
window.addEventListener("unhandledrejection", (event) => {
  console.error(event.reason);
  setStatus(`Error: ${String(event.reason)}`, "error");
});

function show(text: string, kind: "speech" | "hazard" = "speech"): void {
  const line = document.createElement("p");
  line.textContent = text;
  if (kind === "hazard") line.className = "hazard";
  transcript.prepend(line);
  while (transcript.childElementCount > 8) transcript.lastElementChild?.remove();
}

// The places the app remembers, and the panel a sighted helper edits them
// in. Every edit is spoken, since a changed memory is a state the user
// cannot see.
// The blueprint: each scan drawn from above, with the floor marked
// unobstructed or obstructed, and a remembered place drawn from its views.
// The two sheets share the bottom of the screen, so one closes the other.
const blueprint = initBlueprint({ onOpen: () => places.close() });
const places = initPlaces({
  speak: (text) => {
    tts.say(text, SpeechPriority.Answer);
    show(text);
  },
  onMemory: (memory) => blueprint.onMemory(memory),
  onOpen: () => blueprint.close(),
});

// A hook for driving the screen without a camera or a socket, from the
// browser console: __visionos.event({type: "speech", text: "..."}).
(window as unknown as { __visionos: unknown }).__visionos = { event: (event: ServerEvent) => onServerEvent(event) };

// What this backend can do, in one sentence, before the user commits to
// starting. The same facts are spoken once connected; showing them here too
// means a sighted helper can see at a glance whether a key is missing.
type Health = {
  provider_active?: string;
  provider_note?: string;
  ocr?: string;
  lan_address?: string | null;
};

function describeMode(health: Health | null): string {
  if (!health) return "Backend not reachable yet. It will keep trying once you start.";
  const ocr =
    health.ocr && health.ocr !== "none"
      ? `Reads text on-device (${health.ocr}).`
      : "No on-device text reader.";
  switch (health.provider_active) {
    case "ClaudeVisionProvider":
      return `Full mode: Claude describes the scene and answers questions. ${ocr}`;
    case "NvidiaVisionProvider":
      return `Ask goes online: an NVIDIA vision model answers your questions; scans and reads stay on-device. ${ocr}${health.provider_note ? ` ${health.provider_note}` : ""}`;
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
    // The dot beside it: green when questions go to a model, amber for
    // on-device only, red when the backend cannot be reached.
    modeLine.dataset.tone = !health ? "bad" : health.provider_active === "LocalSceneProvider" ? "warn" : "ok";
    // On the computer that runs the servers, say where a phone should go.
    // The phone needs the HTTPS port; the address changes with the network.
    const local = location.hostname === "localhost" || location.hostname === "127.0.0.1";
    if (health?.lan_address && local) {
      modeLine.textContent += ` Phone on the same Wi-Fi: https://${health.lan_address}:5173`;
    }
  } catch {
    modeLine.textContent = describeMode(null);
    modeLine.dataset.tone = "bad";
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
  lastServerMessageAt = performance.now();
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
      setStatus(mode?.label ?? "Connected", "ok");
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
      blueprint.onInventory(event.items);
      const boxed = event.items.filter((item) => item.box !== null) as Boxed[];
      if (boxed.length > 0) {
        scanBoxesUntil = performance.now() + SCAN_BOXES_MS;
        drawBoxes(boxed);
      }
      break;
    }

    case "detections": {
      // Live boxes, unless a scan's are still on show. The gap between
      // answers is how fast this server actually is, and a box is allowed
      // to live a little longer than that, so boxes do not blink between
      // frames on a slow machine and still vanish quickly on a fast one.
      const now = performance.now();
      if (lastLiveBoxesAt > 0) {
        boxTtlMs = Math.min(BOX_TTL_MAX_MS, Math.max(BOX_TTL_MIN_MS, (now - lastLiveBoxesAt) * 2.5));
      }
      lastLiveBoxesAt = now;
      frameInFlight = false;
      if (now >= scanBoxesUntil) drawBoxes(event.items);
      break;
    }

    case "place":
      // What the memory made of the scan; the spoken part rode along in
      // the scan's own speech.
      places.onEvent(event);
      blueprint.onPlaceEvent(event);
      break;

    case "trace":
      // The read or scan is done with its hi-res frames; let the fast loop
      // resume.
      if (event.label.startsWith("read")) readPending = false;
      if (event.label.startsWith("scan")) scanPending = false;
      // The pill said "Scanning…" or "Reading…" until now; say it is done,
      // or it looks stuck to anyone watching the screen.
      setStatus("Ready", "ok");
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
      setStatus("Connected", "ok");
      return;
    }
    setStatus("Reconnecting…", "error");
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
      setStatus("Listening… tap Ask again to stop", "listening");
    } else {
      setStatus("Connected", "ok");
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
      // ?exposure=reset puts the webcam's image controls back to the middle
      // of their ranges, for a camera left dim by an interrupted read.
      const query = new URLSearchParams(location.search);
      await camera.start(query.get("camera"), query.get("exposure"));
    } catch (err) {
      reportFailure((err as Error).message);
      return;
    }

    started = true;
    startButton.hidden = true;
    placeReadArea();
    tapLayer.hidden = false;
    controls.hidden = false;
    places.reveal();
    blueprint.reveal();
    setStatus("Connecting…", "busy");
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
      if (!camera.isRunning || !connection.isOpen || readPending || scanPending) return;
      const now = performance.now();
      if (now - lastPeekAt >= peekIntervalMs) {
        lastPeekAt = now;
        const peek = await camera.capturePeek();
        if (peek && !readPending && !scanPending) connection.sendPeekFrame(peek);
        return;
      }
      if (now - lastFrameAt < frameIntervalMs) return;
      if (frameInFlight && now - frameSentAt < FRAME_IN_FLIGHT_MAX_MS) return;
      lastFrameAt = now;
      const frame = await camera.captureFast();
      // Re-checked after the await: a read or scan may have started while
      // capturing.
      if (frame && !readPending && !scanPending) {
        frameInFlight = true;
        frameSentAt = performance.now();
        connection.sendFrame(frame);
      }
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
// Live frames pause while a scan is in flight, as they do for a read: on a
// CPU both would otherwise queue behind the detector's next live frame.
// Cleared by the scan's trace event; this is the backstop.
const SCAN_TIMEOUT_MS = 8000;
let scanPending = false;
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
  if (scanPending) {
    // A second tap while a scan runs is impatience, not a new request.
    setStatus("Still scanning…", "busy");
    return;
  }
  spatial.play("info", 0, 1);
  setStatus("Scanning…", "busy");
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
  scanPending = true;
  window.setTimeout(() => {
    scanPending = false;
  }, SCAN_TIMEOUT_MS);
  connection.sendScanFrames(captured);
}

// The last detections drawn, so a resize can redraw them, and how long a
// scan's boxes stay up before the live ones take over again.
let drawn: Boxed[] = [];
let scanBoxesUntil = 0;
const SCAN_BOXES_MS = 1500;
// A box lives only as long as the frame it came from is current. If no
// fresh frame has been looked at within this long, whatever is drawn is
// stale and goes, so a box never outlives the thing it was drawn around.
// The allowance follows the server's own pace (see the detections event):
// 350 ms on a fast machine, up to two seconds on a slow one.
const BOX_TTL_MIN_MS = 350;
const BOX_TTL_MAX_MS = 2000;
let boxTtlMs = BOX_TTL_MIN_MS;
let lastLiveBoxesAt = 0;
window.setInterval(() => {
  const now = performance.now();
  if (drawn.length === 0 || now < scanBoxesUntil) return;
  if (now - lastLiveBoxesAt > boxTtlMs) drawBoxes([]);
}, 100);

/**
 * A red outline and label around the people, furniture and things of size
 * the detector believes it sees, for a sighted helper checking the glasses;
 * hand-held things (a phone, a bottle) get no box. Each set replaces the
 * last outright: a box that is not in the newest frame is gone. Boxes
 * arrive normalized to the frame; the frame is letterboxed on screen, so
 * they are mapped through the same geometry as the read area.
 */
const UNBOXED = new Set(["wall"]);

function drawBoxes(items: Boxed[]): void {
  drawn = items;
  const frame = camera.frameOnScreen();
  const scale = window.devicePixelRatio || 1;
  boxes.width = Math.round(window.innerWidth * scale);
  boxes.height = Math.round(window.innerHeight * scale);
  const ctx = boxes.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  if (!frame) return;
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#e53935";
  ctx.font = "13px system-ui, sans-serif";
  for (const item of items) {
    // Hand-held things clutter the picture and are spoken only when asked
    // about, so they are not drawn either; a wall is the whole picture.
    if (item.scale === "small" || UNBOXED.has(item.label)) continue;
    const [x1, y1, x2, y2] = item.box;
    const left = frame.left + x1 * frame.width;
    const top = frame.top + y1 * frame.height;
    const width = (x2 - x1) * frame.width;
    const height = (y2 - y1) * frame.height;
    ctx.strokeRect(left, top, width, height);
    const text = `${item.label} ${Math.round(item.confidence * 100)}%`;
    const pad = 4;
    const textWidth = ctx.measureText(text).width;
    const tagTop = top >= 18 ? top - 18 : top;
    ctx.fillStyle = "#e53935";
    ctx.fillRect(left, tagTop, textWidth + pad * 2, 18);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, left + pad, tagTop + 13);
  }
}
window.addEventListener("resize", () => drawBoxes(drawn));

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

async function readText(): Promise<void> {
  if (!connection.isOpen) {
    reportFailure("Not connected yet.");
    return;
  }
  // Audible acknowledgement: reading takes a moment and the tap is otherwise
  // silent, which reads as a missed tap.
  if (readPending) {
    setStatus("Still reading…", "busy");
    return;
  }
  spatial.play("info", 0, 1);
  setStatus("Reading…", "busy");
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
  // Daniel unless the page URL says otherwise (?voice=<part of a name>). A
  // choice remembered by the old Voice button is ignored, so the app stays
  // on it; without a Daniel, the best voice this browser has.
  const override = new URLSearchParams(location.search).get("voice");
  const chosen = pickVoice(override ?? VOICE_NAME);
  if (!chosen) return;

  tts.setVoice(chosen);
  console.info(
    "[voices] using %s — available: %s",
    chosen.name,
    rankVoices().map((entry) => `${entry.voice.name} (${entry.score})`).join(", ")
  );
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
  stop: () => {
    tts.stopAll();
    spatial.stopBeacon();
    connection.sendIntent("stop_beacon");
  },
};
type Action = keyof typeof actions;

/** Every press answers on screen at once, whichever way it arrived: a tap
 * here, a key, or the watch. A pressed button lights for a moment even when
 * no finger touched it, so a sighted helper sees the watch working. */
function flash(name: Action): void {
  const button = document.getElementById(name);
  if (!button) return;
  button.classList.add("pressed");
  window.setTimeout(() => button.classList.remove("pressed"), 220);
}

function run(name: Action): void {
  flash(name);
  actions[name]();
}

startButton.addEventListener("click", begin);
tapLayer.addEventListener("click", () => run("scan"));
for (const name of Object.keys(actions) as Action[]) {
  el(name).addEventListener("click", (e) => {
    e.stopPropagation();
    run(name);
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
  // P pulls the places panel up or puts it away, B the blueprint; Escape
  // puts either away too, on its way to stopping speech.
  if (key === "p") {
    event.preventDefault();
    places.toggle();
    return;
  }
  if (key === "b") {
    event.preventDefault();
    blueprint.toggle();
    return;
  }
  if (key === "Escape") {
    if (places.isOpen) places.close();
    if (blueprint.isOpen) blueprint.close();
  }
  const action = KEYS[key];
  if (!action) return;
  event.preventDefault();
  run(action);
});

// The watch's Arduino can instead talk over its USB serial port, one command
// per line: SCAN, READ, ASK or STOP (a VOICE line from an older sketch is
// ignored). That needs no keyboard emulation,
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
      setStatus("Watch connected", "ok");
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
          if (started) run(line as Action);
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
  setStatus("This browser has no speech synthesis. Try Safari or Chrome.", "error");
}
if (!voice.isSupported) {
  askButton.hidden = true;
}
