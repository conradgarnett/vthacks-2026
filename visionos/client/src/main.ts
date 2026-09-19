/**
 * Client wiring: camera, voice, spatial audio, speech, haptics.
 */

import { Camera } from "./camera";
import { SpatialAudio } from "./audio/spatial";
import { SpeechPriority, TtsPlayer } from "./audio/tts-player";
import { onVoicesReady, pickVoice, rankVoices, saveVoiceName } from "./audio/voices";
import { Voice } from "./voice";
import { Connection, type ServerEvent } from "./ws";

const DISCLAIMER =
  "VisionOS ready. Tap anywhere to scan. This is an assistive tool, not a " +
  "replacement for your cane or guide dog. Distances are estimates.";

const FRAME_INTERVAL_MS = 700;

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
const startButton = el<HTMLButtonElement>("start");
const tapLayer = el<HTMLButtonElement>("tap-layer");
const controls = el<HTMLDivElement>("controls");
const askButton = el<HTMLButtonElement>("ask");
const status = el<HTMLParagraphElement>("status");
const transcript = el<HTMLDivElement>("transcript");

const tts = new TtsPlayer();
const spatial = new SpatialAudio();
const camera = new Camera(video);

// Spoken announcements are once-per-session. Reconnects re-fire the events
// that trigger them, and a reconnect loop turns that into an endless monologue.
let announcedMode = false;
let announcedDisconnect = false;
let starting = false;
let started = false;

// While true the fast frame loop is suspended so a 640px frame cannot
// overwrite the hi-res one a read depends on.
let readPending = false;
const READ_TIMEOUT_MS = 8000;
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
      setStatus("Listening…");
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
      if (!camera.isRunning || !connection.isOpen || readPending) return;
      const frame = await camera.captureFast();
      // Re-checked after the await: a read may have started while capturing,
      // and a 640px frame landing after the hi-res one is what made OCR miss.
      if (frame && !readPending) connection.sendFrame(frame);
    }, FRAME_INTERVAL_MS);
  } catch (err) {
    // Anything unexpected must still surface. A silent throw here is
    // indistinguishable from a frozen app to someone who cannot see it.
    reportFailure(`Couldn't start: ${(err as Error).message}`);
  } finally {
    starting = false;
  }
}

async function sendDetailedThen(intent: string): Promise<void> {
  // The frame loop is suspended for the whole operation. WebSocket delivery
  // is ordered, so with no fast frame interleaved the burst below is exactly
  // what the server reads from.
  readPending = true;
  try {
    // A burst, not one frame. Hand-held capture blurs and glares differently
    // each time, and the server keeps only text that several frames agree on
    // -- which is what separates real text from OCR noise.
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

    captured.forEach((frame) => connection.sendFrame(frame));
    connection.sendIntent(intent);
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

startButton.addEventListener("click", begin);
tapLayer.addEventListener("click", () => connection.sendIntent("scan"));
el("scan").addEventListener("click", (e) => {
  e.stopPropagation();
  connection.sendIntent("scan");
});
el("read").addEventListener("click", (e) => {
  e.stopPropagation();
  void sendDetailedThen("read");
});
el("voice").addEventListener("click", (e) => {
  e.stopPropagation();
  cycleVoice();
});
el("stop").addEventListener("click", (e) => {
  e.stopPropagation();
  tts.stopAll();
  spatial.stopBeacon();
  connection.sendIntent("stop_beacon");
});

// Push to talk. Pointer events cover touch and mouse; releasing anywhere ends
// the capture so a drag off the button cannot leave the mic open.
askButton.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  e.stopPropagation();
  voice.start();
});
const endCapture = () => voice.isListening && voice.stop();
askButton.addEventListener("pointerup", endCapture);
askButton.addEventListener("pointercancel", endCapture);
window.addEventListener("pointerup", endCapture);

if (!tts.isSupported) {
  setStatus("This browser has no speech synthesis. Try Safari or Chrome.");
}
if (!voice.isSupported) {
  askButton.hidden = true;
}
