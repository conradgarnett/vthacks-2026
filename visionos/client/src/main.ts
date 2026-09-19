/**
 * Client wiring: camera, voice, spatial audio, speech.
 *
 * The interaction model, for someone who cannot see the screen:
 *
 *   tap anywhere   scan the room
 *   Read           read text in view
 *   hold Ask       speak a question or a command
 *   Stop           silence everything
 */

import { Camera } from "./camera";
import { SpatialAudio } from "./audio/spatial";
import { SpeechPriority, TtsPlayer } from "./audio/tts-player";
import { onVoicesReady, pickVoice, rankVoices, saveVoiceName } from "./audio/voices";
import { Voice } from "./voice";
import { Connection, type ServerEvent } from "./ws";

const DISCLAIMER =
  "VisionOS ready. Tap anywhere to scan, hold Ask to speak. This is an " +
  "assistive tool, not a replacement for your cane or guide dog. Distances " +
  "are estimates.";

const FRAME_INTERVAL_MS = 700;
const TRANSCRIPT_LINES = 3;
const VOICE_SAMPLE =
  "This is how I'll sound. A doorway is about three meters ahead, at your two o'clock.";

// Enough frames for majority agreement; the gap lets the scene shift slightly
// so blur and glare differ between them, which is what makes the vote useful.
const READ_BURST_FRAMES = 3;
const READ_BURST_GAP_MS = 120;

// Spoken commands that select a mode rather than ask a question. Matched
// before anything is sent, because guidance and reading are not answers.
const BEACON_PHRASES = [
  /^(?:take|guide|lead|walk) me to (?:the |a |an )?(.+)$/i,
  /^(?:navigate|go) to (?:the |a |an )?(.+)$/i,
  /^(?:find|locate) (?:the |a |an )?(.+?)(?: for me)?$/i,
];
const STOP_PHRASES = /^(?:stop|cancel|quiet|never mind|nevermind)\b/i;
const READ_PHRASES =
  /^(?:read(?: (?:this|that|it|the sign|the text|the label|the menu))?|what does (?:it|this|that|the sign) say)$/i;
const VOICE_PHRASES = /^(?:next|change|switch) voice$/i;

const el = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

const video = el<HTMLVideoElement>("camera");
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

// Spoken announcements are once per session. Reconnects re-fire the events
// that trigger them, and a reconnect loop would turn that into a monologue.
let announcedMode = false;
let announcedDisconnect = false;
let starting = false;
let started = false;
let idleStatus = "Connected";
let voiceIndex = 0;

const setStatus = (text: string): void => {
  status.textContent = text;
};

function show(text: string, kind: "speech" | "hazard" = "speech"): void {
  const line = document.createElement("p");
  line.textContent = text;
  if (kind === "hazard") line.className = "hazard";
  transcript.prepend(line);
  while (transcript.childElementCount > TRANSCRIPT_LINES) {
    transcript.lastElementChild?.remove();
  }
}

/** Surface a failure the user cannot see. Silence reads as a freeze. */
function reportFailure(message: string): void {
  setStatus(message);
  show(message);
  tts.say(message, SpeechPriority.Answer);
}

// Without these, an uncaught error during startup leaves the start screen up
// with no explanation.
window.addEventListener("error", (event) => {
  console.error(event.error ?? event.message);
  setStatus(`Error: ${event.message}`);
});
window.addEventListener("unhandledrejection", (event) => {
  console.error(event.reason);
  setStatus(`Error: ${String(event.reason)}`);
});

// What this backend can do, in one sentence, before the user commits to
// starting. The same facts are spoken once connected; showing them here too
// means a sighted helper can see at a glance whether a key is missing.
type Health = { provider_active?: string; ocr?: string };

function describeMode(health: Health | null): string {
  if (!health) return "Backend not reachable yet. It will keep trying once you start.";
  const ocr = health.ocr && health.ocr !== "none" ? `Reads text on-device (${health.ocr}).` : "No on-device text reader.";
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
    modeLine.textContent = describeMode(response.ok ? ((await response.json()) as Health) : null);
  } catch {
    modeLine.textContent = describeMode(null);
  }
}

const socketUrl = (): string => {
  const override = new URLSearchParams(location.search).get("backend");
  if (override) return override;
  // Same origin: Vite proxies /ws, so the phone accepts one certificate.
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
};

function onServerEvent(event: ServerEvent): void {
  switch (event.type) {
    case "ready": {
      const modes: Record<string, { label: string; spoken?: string }> = {
        ReplayVisionProvider: {
          label: "Replay — scripted, ignores camera",
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
      idleStatus = mode?.label ?? "Connected";
      setStatus(idleStatus);
      if (mode?.spoken && !announcedMode) {
        announcedMode = true;
        tts.say(mode.spoken, SpeechPriority.Answer);
      }
      break;
    }

    case "speech":
      setStatus(idleStatus);
      tts.say(event.text, SpeechPriority.Answer);
      show(event.text);
      break;

    case "hazard":
      // Earcon first: it lands in ~20 ms from the right direction, while the
      // sentence takes about a second to say.
      spatial.play("hazard", event.azimuth_deg, event.distance_m || 1);
      navigator.vibrate?.(event.severity >= 2 ? [120, 50, 120] : [80]);
      tts.say(event.text, SpeechPriority.Hazard);
      show(event.text, "hazard");
      break;

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

    case "trace":
      setStatus(idleStatus);
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
      setStatus(idleStatus);
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
      setStatus("Listening…");
    } else {
      setStatus(idleStatus);
    }
  },
  onError: (message) => {
    setStatus(message);
    tts.say(message, SpeechPriority.Answer);
  },
});

// --- Actions ----------------------------------------------------------------

function scan(): void {
  if (!connection.isOpen) {
    reportFailure("Not connected yet.");
    return;
  }
  setStatus("Scanning…");
  connection.sendIntent("scan");
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

  // A burst, not one frame. Hand-held capture blurs and glares differently
  // each time, and the server keeps only text that the frames agree on,
  // which is what separates real text from OCR noise.
  const captured: Blob[] = [];
  for (let i = 0; i < READ_BURST_FRAMES; i++) {
    const frame = await camera.captureDetailed();
    if (frame) captured.push(frame);
    if (i < READ_BURST_FRAMES - 1) {
      await new Promise((resolve) => setTimeout(resolve, READ_BURST_GAP_MS));
    }
  }

  if (captured.length === 0) {
    reportFailure("Couldn't capture the image to read.");
    return;
  }
  connection.sendReadFrames(captured);
}

function stopEverything(): void {
  tts.stopAll();
  spatial.stopBeacon();
  connection.sendIntent("stop_beacon");
  setStatus(idleStatus);
}

function routeSpokenCommand(text: string): void {
  const command = text.trim().replace(/[?.!]+$/, "");

  if (STOP_PHRASES.test(command)) {
    stopEverything();
    return;
  }
  if (READ_PHRASES.test(command)) {
    void readText();
    return;
  }
  if (VOICE_PHRASES.test(command)) {
    cycleVoice();
    return;
  }
  for (const pattern of BEACON_PHRASES) {
    const match = command.match(pattern);
    if (match?.[1]) {
      connection.sendIntent("locate", match[1]);
      return;
    }
  }
  connection.sendIntent("ask", text.trim());
}

async function begin(): Promise<void> {
  // Repeated taps on an apparently stuck screen would otherwise re-run the
  // whole sequence and stack up duplicate disclaimers and frame loops.
  if (starting || started) return;
  starting = true;

  try {
    // Both must happen inside the tap handler: iOS starts audio suspended and
    // refuses speech synthesis until a gesture has occurred. Neither throws.
    tts.prime();
    spatial.init();

    try {
      await camera.start();
    } catch (err) {
      reportFailure((err as Error).message);
      return;
    }

    started = true;
    startButton.hidden = true;
    tapLayer.hidden = false;
    controls.hidden = false;
    setStatus("Connecting…");
    tts.say(DISCLAIMER, SpeechPriority.Answer);
    connection.connect();

    setInterval(async () => {
      if (!camera.isRunning || !connection.isOpen) return;
      const frame = await camera.captureFast();
      if (frame) connection.sendFrame(frame);
    }, FRAME_INTERVAL_MS);
  } catch (err) {
    // A silent throw here is indistinguishable from a frozen app to someone
    // who cannot see it.
    reportFailure(`Couldn't start: ${(err as Error).message}`);
  } finally {
    starting = false;
  }
}

// --- Voices -----------------------------------------------------------------

/** Voices load asynchronously; getVoices() is usually empty at startup. */
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

// --- Wiring -----------------------------------------------------------------

onVoicesReady(applyBestVoice);
void showMode();

startButton.addEventListener("click", () => void begin());
tapLayer.addEventListener("click", scan);
el("read").addEventListener("click", () => void readText());
el("stop").addEventListener("click", stopEverything);

// Push to talk. Pointer events cover touch and mouse; releasing anywhere ends
// the capture so a drag off the button cannot leave the mic open.
askButton.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  voice.start();
});
const endCapture = () => {
  if (voice.isListening) voice.stop();
};
askButton.addEventListener("pointerup", endCapture);
askButton.addEventListener("pointercancel", endCapture);
window.addEventListener("pointerup", endCapture);

if (!tts.isSupported) {
  setStatus("This browser has no speech synthesis. Try Safari or Chrome.");
}
if (!voice.isSupported) {
  askButton.hidden = true;
}
