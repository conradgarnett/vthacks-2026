/**
 * Speech output with priority-based interruption.
 *
 * Uses the browser's speechSynthesis: no API key, no network, so speech
 * survives the WiFi dying. A hazard alert must be able to cut off whatever is
 * currently being said -- the router's priority rule is enforced here, at the
 * last possible moment before sound reaches the user.
 */

export enum SpeechPriority {
  Ambient = 10,
  Beacon = 20,
  Answer = 30,
  Hazard = 40,
}

type Utterance = { text: string; priority: SpeechPriority };

export class TtsPlayer {
  private queue: Utterance[] = [];
  private speaking: Utterance | null = null;
  private primed = false;
  private rate = 1.05;

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

  setRate(rate: number): void {
    this.rate = Math.min(2, Math.max(0.5, rate));
  }

  say(text: string, priority: SpeechPriority = SpeechPriority.Answer): void {
    if (!this.isSupported || !text.trim()) return;

    if (this.speaking && priority > this.speaking.priority) {
      // Drop anything the interrupting message outranks, so we don't resume
      // stale narration after an obstacle warning.
      this.queue = this.queue.filter((u) => u.priority >= priority);
      window.speechSynthesis.cancel();
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

    const utterance = new SpeechSynthesisUtterance(next.text);
    utterance.rate = next.priority === SpeechPriority.Hazard ? 1.15 : this.rate;
    const finish = () => {
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
    window.speechSynthesis?.cancel();
  }
}
