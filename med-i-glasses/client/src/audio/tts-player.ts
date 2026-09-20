/**
 * Speech output with priority-based interruption.
 *
 * Two voices, chosen by priority.
 *
 * Hazards use the browser's speechSynthesis: no API key, no network, so the
 * warning still arrives when the WiFi dies -- which is exactly the moment it
 * matters. A warning that waits on a request is a warning that arrives after
 * the stairs.
 *
 * Everything else asks the backend for a better voice first (/speech, which
 * is cache-first and answers in milliseconds for anything said before) and
 * falls back to speechSynthesis the instant that is not available. A 404 is
 * the normal answer when there is no key or no quota, not an error.
 *
 * A hazard alert must be able to cut off whatever is currently being said --
 * the router's priority rule is enforced here, at the last possible moment
 * before sound reaches the user, and it cancels audio playback and synthesis
 * alike.
 */

export enum SpeechPriority {
  Answer = 30,
  Hazard = 40,
}

type Utterance = { text: string; priority: SpeechPriority };

export class TtsPlayer {
  private queue: Utterance[] = [];
  private speaking: Utterance | null = null;
  private primed = false;
  // Slightly under 1 reads as considered rather than hurried. Default browser
  // rates sound clipped, which is tiring over a long session.
  private rate = 0.98;
  // Below 1 deepens the voice. Much lower starts sounding synthetic.
  private pitch = 0.88;
  private voice: SpeechSynthesisVoice | null = null;
  // Backend origin for /speech. Empty disables the good voice entirely.
  private base = "";
  private el: HTMLAudioElement | null = null;
  // Bumped whenever speech is cancelled. An in-flight fetch compares it after
  // awaiting and drops its audio if it lost the race, so an interrupted
  // sentence cannot arrive late and talk over the warning that replaced it.
  private generation = 0;

  /** Where /speech lives. Unset means browser voice for everything. */
  setBackend(origin: string): void {
    this.base = origin.replace(/\/$/, "");
  }

  /** Apply a chosen voice. Null falls back to the browser default. */
  setVoice(voice: SpeechSynthesisVoice | null): void {
    this.voice = voice;
  }

  /**
   * iOS refuses to speak unless the first utterance follows a user gesture.
   * Call this from a tap handler or the demo opens in total silence.
   */
  prime(): void {
    if (this.primed || !("speechSynthesis" in window)) return;
    const silent = new SpeechSynthesisUtterance("");
    silent.volume = 0;
    window.speechSynthesis.speak(silent);
    this.primed = true;
  }

  get isSupported(): boolean {
    return "speechSynthesis" in window;
  }

  say(text: string, priority: SpeechPriority = SpeechPriority.Answer): void {
    if (!this.isSupported || !text.trim()) return;

    if (this.speaking && priority > this.speaking.priority) {
      // Drop anything the interrupting message outranks, so we don't resume
      // stale narration after an obstacle warning.
      this.queue = this.queue.filter((u) => u.priority >= priority);
      this.cut();
      this.speaking = null;
    }

    this.queue.push({ text, priority });
    this.pump();
  }

  private pump(): void {
    if (this.speaking || this.queue.length === 0) return;

    // Stable sort by descending priority: equal priorities keep arrival order,
    // which keeps sentences of one answer in the order they were written.
    this.queue.sort((a, b) => b.priority - a.priority);
    const next = this.queue.shift()!;
    this.speaking = next;

    // Hazards never wait on the network; everything else tries for the
    // better voice and falls back without complaint.
    if (next.priority !== SpeechPriority.Hazard && this.base) {
      void this.speakVoiced(next);
      return;
    }
    this.speakLocally(next);
  }

  /** Backend voice, with the browser as the fallback for every failure. */
  private async speakVoiced(next: Utterance): Promise<void> {
    const mine = this.generation;
    let url: string | null = null;
    try {
      const response = await fetch(
        `${this.base}/speech?text=${encodeURIComponent(next.text)}`,
      );
      if (!response.ok) throw new Error(String(response.status));
      url = URL.createObjectURL(await response.blob());
    } catch {
      // No key, no quota, no network, or simply slow. Say it ourselves.
      if (this.generation === mine) this.speakLocally(next);
      return;
    }

    // Cancelled while we were waiting: drop it rather than speak over what
    // took its place.
    if (this.generation !== mine) {
      URL.revokeObjectURL(url);
      return;
    }

    const el = new Audio(url);
    this.el = el;
    const finish = () => {
      URL.revokeObjectURL(url!);
      if (this.el === el) this.el = null;
      if (this.generation !== mine) return;
      this.speaking = null;
      this.pump();
    };
    el.onended = finish;
    // A decode failure is not worth a silent gap: say it the other way.
    el.onerror = () => {
      URL.revokeObjectURL(url!);
      if (this.el === el) this.el = null;
      if (this.generation === mine) this.speakLocally(next);
    };
    void el.play().catch(() => {
      if (this.generation === mine) this.speakLocally(next);
    });
  }

  /** Stop whatever is making sound, by either route. */
  private cut(): void {
    this.generation += 1;
    window.speechSynthesis?.cancel();
    if (this.el) {
      this.el.pause();
      this.el = null;
    }
  }

  private speakLocally(next: Utterance): void {
    const mine = this.generation;
    const utterance = new SpeechSynthesisUtterance(next.text);
    if (this.voice) {
      utterance.voice = this.voice;
      // Safari can mismatch voice and lang and fall back to a default voice.
      utterance.lang = this.voice.lang;
    }
    // Hazards speak faster and flatter: urgency, not warmth.
    const urgent = next.priority === SpeechPriority.Hazard;
    utterance.rate = urgent ? 1.12 : this.rate;
    utterance.pitch = urgent ? 1.0 : this.pitch;
    const finish = () => {
      if (this.generation !== mine) return;
      this.speaking = null;
      this.pump();
    };
    utterance.onend = finish;
    utterance.onerror = finish;

    window.speechSynthesis.speak(utterance);
  }

  stopAll(): void {
    this.queue = [];
    this.speaking = null;
    this.cut();
  }
}
