/**
 * Walking skeleton wiring: camera -> socket -> speech.
 *
 * Voice input and spatial audio arrive in later phases; this file proves the
 * end-to-end path a blind user actually depends on.
 */

import { Camera } from "./camera";
import { SpeechPriority, TtsPlayer } from "./audio/tts-player";
import { Connection, type ServerEvent } from "./ws";

const DISCLAIMER =
  "VisionOS is starting. This is an assistive tool, not a replacement for " +
  "your cane or guide dog. Distances are estimates.";

// One frame per second is enough while the backend only keeps the latest.
// Phase 2 raises this once perception actually consumes the stream.
const FRAME_INTERVAL_MS = 1000;

const el = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

const video = el<HTMLVideoElement>("camera");
const startButton = el<HTMLButtonElement>("start");
const controls = el<HTMLDivElement>("controls");
const status = el<HTMLParagraphElement>("status");
const transcript = el<HTMLDivElement>("transcript");

const tts = new TtsPlayer();
const camera = new Camera(video);

const backendUrl = (): string => {
  const override = new URLSearchParams(location.search).get("backend");
  if (override) return override;
  // Same origin: Vite proxies /ws to the backend. Connecting straight to
  // :8000 would need a second accepted certificate, which Safari will not
  // prompt for on a WebSocket -- it just reconnects forever.
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/ws`;
};

/** Sets the visible status and announces it to screen readers. */
function setStatus(text: string): void {
  status.textContent = text;
}

function show(text: string): void {
  const line = document.createElement("p");
  line.textContent = text;
  transcript.prepend(line);
  while (transcript.childElementCount > 12) transcript.lastElementChild?.remove();
}

function onServerEvent(event: ServerEvent): void {
  switch (event.type) {
    case "ready":
      setStatus(
        event.provider === "replay" ? "Connected (replay mode)" : "Connected"
      );
      break;
    case "speech":
      tts.say(event.text, SpeechPriority.Answer);
      show(event.text);
      break;
    case "hazard":
      tts.say(event.text, SpeechPriority.Hazard);
      navigator.vibrate?.([80, 40, 80]);
      show(`[hazard] ${event.text}`);
      break;
    case "trace":
      console.info(
        `[latency] ${event.label} first_word=${event.stages.first_sentence ?? "-"}ms total=${event.total_ms}ms`
      );
      break;
  }
}

const connection = new Connection(backendUrl(), {
  onEvent: onServerEvent,
  onConnectionChange: (connected) => {
    if (connected) return;
    setStatus("Reconnecting...");
    // Spoken, because the user cannot see the status line.
    tts.say("I lost connection. Reconnecting.", SpeechPriority.Answer);
  },
});

async function begin(): Promise<void> {
  // Must happen inside the tap handler or iOS stays silent all session.
  tts.prime();
  tts.say(DISCLAIMER, SpeechPriority.Answer);

  try {
    await camera.start();
  } catch (err) {
    const message = (err as Error).message;
    setStatus(message);
    tts.say(message, SpeechPriority.Hazard);
    return;
  }

  startButton.hidden = true;
  controls.hidden = false;
  setStatus("Connecting...");
  connection.connect();

  setInterval(async () => {
    if (!camera.isRunning || !connection.isOpen) return;
    const frame = await camera.captureFast();
    if (frame) connection.sendFrame(frame);
  }, FRAME_INTERVAL_MS);
}

async function sendDetailedThen(intent: string): Promise<void> {
  // OCR needs detail the fast path throws away, so push a hi-res frame first.
  const frame = await camera.captureDetailed();
  if (frame) connection.sendFrame(frame);
  connection.sendIntent(intent);
}

startButton.addEventListener("click", begin);
el("scan").addEventListener("click", () => connection.sendIntent("scan"));
el("read").addEventListener("click", () => void sendDetailedThen("read"));
el("stop").addEventListener("click", () => tts.stopAll());

if (!tts.isSupported) {
  setStatus("This browser has no speech synthesis. Try Safari or Chrome.");
}
