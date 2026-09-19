/** High-level intents every input device produces. The app only ever sees intents, never raw devices. */
export type Intent = 'select' | 'next' | 'back' | 'activate' | 'acknowledge';

export interface IntentEvent {
  intent: Intent;
  /** Which device produced it, e.g. "switch-scan", "dwell-gaze", "keyboard", "scripted". */
  device: string;
  /** Milliseconds on the scheduler clock. */
  at: number;
  /** For pointer-like devices: the target the intent refers to. */
  targetId?: string;
}

export type IntentSink = (e: IntentEvent) => void;

/**
 * An input device behind one interface. Every device supports pausing: "pause tracking" must always
 * be available so an accidental gesture or a tired user can never be trapped by the interface.
 */
export interface InputDevice {
  readonly id: string;
  readonly label: string;
  /** False when the hardware or model needed by this device is not available. */
  readonly available: boolean;
  readonly unavailableReason?: string;
  readonly paused: boolean;
  start(emit: IntentSink): void;
  stop(): void;
  pause(paused: boolean): void;
}

/** Time source. Real in the app, manual in tests and the headless demo so runs are deterministic. */
export interface Scheduler {
  now(): number;
  setInterval(fn: () => void, ms: number): () => void;
  setTimeout(fn: () => void, ms: number): () => void;
}

export const realScheduler: Scheduler = {
  now: () => Date.now(),
  setInterval: (fn, ms) => {
    const h = setInterval(fn, ms);
    return () => clearInterval(h);
  },
  setTimeout: (fn, ms) => {
    const h = setTimeout(fn, ms);
    return () => clearTimeout(h);
  },
};

/** Deterministic scheduler: nothing runs until `advance()` is called. */
export class ManualScheduler implements Scheduler {
  private t = 0;
  private seq = 0;
  private readonly timers = new Map<number, { at: number; fn: () => void; every?: number }>();

  now(): number {
    return this.t;
  }

  setInterval(fn: () => void, ms: number): () => void {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, fn, every: ms });
    return () => void this.timers.delete(id);
  }

  setTimeout(fn: () => void, ms: number): () => void {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, fn });
    return () => void this.timers.delete(id);
  }

  /** Move time forward, running due timers in order. */
  advance(ms: number): void {
    const end = this.t + ms;
    for (;;) {
      let nextId = -1;
      let nextAt = Infinity;
      for (const [id, tm] of this.timers) {
        if (tm.at <= end && tm.at < nextAt) {
          nextAt = tm.at;
          nextId = id;
        }
      }
      if (nextId < 0) break;
      const tm = this.timers.get(nextId);
      if (!tm) break;
      this.t = tm.at;
      if (tm.every) tm.at += tm.every;
      else this.timers.delete(nextId);
      tm.fn();
    }
    this.t = end;
  }
}
