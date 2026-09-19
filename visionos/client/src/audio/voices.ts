/**
 * Voice selection.
 *
 * The browser's default pick is usually the worst usable option, and on macOS
 * the list is full of novelty voices -- Zarvox, Bubbles, Bad News. Shipping a
 * default that might select one of those is not acceptable for a tool someone
 * relies on, so novelty voices are excluded outright rather than ranked low.
 *
 * Ranking prefers, in order: downloaded Enhanced/Premium variants (a far
 * bigger quality jump than anything else here), then known-good natural
 * voices, then anything on-device. On-device matters twice over -- network
 * voices stall when the connection dies, which is the moment this tool is
 * most needed.
 */

export type VoiceChoice = {
  voice: SpeechSynthesisVoice;
  score: number;
};

const STORAGE_KEY = "visionos.voice";

// Joke voices in the default macOS install. Never acceptable.
const NOVELTY = new Set([
  "albert", "bad news", "bahh", "bells", "boing", "bubbles", "cellos",
  "deranged", "good news", "jester", "organ", "superstar", "trinoids",
  "whisper", "wobble", "zarvox", "hysterical", "pipe organ", "junior",
  "ralph", "fred", "kathy", "grandma", "grandpa", "eddy", "flo", "reed",
  "rocko", "sandy", "shelley", "princess",
]);

// Deeper, richer voices first. Names are stable across Apple platforms even
// though availability is not.
const PREFERRED = [
  "aaron", "daniel", "arthur", "tom", "alex", "evan", "nathan", "gordon",
  "oliver", "ava", "allison", "serena", "zoe", "samantha", "karen", "moira",
  "tessa", "martha", "nicky",
];

const isNovelty = (name: string): boolean => {
  const lower = name.toLowerCase();
  return [...NOVELTY].some((bad) => lower === bad || lower.startsWith(`${bad} (`));
};

function score(voice: SpeechSynthesisVoice): number {
  let points = 0;

  const name = voice.name.toLowerCase();
  // Downloaded high-quality variants dwarf every other factor.
  if (name.includes("premium")) points += 120;
  else if (name.includes("enhanced")) points += 100;

  const rank = PREFERRED.findIndex((p) => name.startsWith(p));
  if (rank >= 0) points += 60 - rank * 2;

  // On-device voices keep working when the network does not.
  if (voice.localService) points += 30;
  if (voice.lang.startsWith("en")) points += 20;
  if (voice.lang === "en-US" || voice.lang === "en-GB") points += 10;
  if (voice.default) points += 5;

  return points;
}

/** Usable voices, best first. Novelty voices are absent, not ranked. */
export function rankVoices(): VoiceChoice[] {
  if (!("speechSynthesis" in window)) return [];

  return window.speechSynthesis
    .getVoices()
    .filter((voice) => voice.lang.startsWith("en") && !isNovelty(voice.name))
    .map((voice) => ({ voice, score: score(voice) }))
    .sort((a, b) => b.score - a.score);
}

/**
 * getVoices() is empty until the list loads in most browsers, so callers must
 * wait for `voiceschanged` rather than reading it once at startup.
 */
export function onVoicesReady(callback: () => void): void {
  if (!("speechSynthesis" in window)) return;

  if (window.speechSynthesis.getVoices().length > 0) {
    callback();
    return;
  }

  const handler = () => {
    window.speechSynthesis.removeEventListener("voiceschanged", handler);
    callback();
  };
  window.speechSynthesis.addEventListener("voiceschanged", handler);
  // Safari sometimes never fires the event; poll once as a backstop.
  window.setTimeout(callback, 1200);
}

export function savedVoiceName(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // private browsing, or site data blocked
  }
}

export function saveVoiceName(name: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, name);
  } catch {
    // A remembered preference is a convenience, not a requirement.
  }
}

/** Voice to use: an explicit override, then a saved choice, then the best. */
export function pickVoice(override?: string | null): SpeechSynthesisVoice | null {
  const ranked = rankVoices();
  if (ranked.length === 0) return null;

  const wanted = override ?? savedVoiceName();
  if (wanted) {
    const match = ranked.find((entry) =>
      entry.voice.name.toLowerCase().includes(wanted.toLowerCase())
    );
    if (match) return match.voice;
  }
  return ranked[0].voice;
}
