import type { InputDevice, IntentEvent } from './types';

export interface ActivationContext {
  /** "intent:<device>", recorded in the audit trail so it is provable that no mouse was used. */
  via: string;
  device: string;
}

export interface Target {
  id: string;
  label: string;
  /** 'acknowledge' targets are what the "acknowledge" intent jumps to. */
  kind: 'action' | 'acknowledge' | 'persona' | 'control';
  activate(ctx: ActivationContext): void | Promise<void>;
}

export interface ActionRecord {
  targetId: string;
  label: string;
  device: string;
  intent: IntentEvent['intent'];
  via: string;
  at: number;
}

export interface ControllerOptions {
  /** A target must have been focused this long before an "activate" from a non-pointer device counts. */
  minFocusMs?: number;
  /** Called when focus moves, so the app can highlight and announce it. */
  onFocus?: (t: Target | undefined) => void;
  onAction?: (a: ActionRecord) => void;
  /** Called before every intent so the target list follows a changing screen. Focus is kept by target id. */
  refreshTargets?: () => Target[];
  now?: () => number;
}

/**
 * Turns intents from any InputDevice into actions on the app. The app registers targets (things a
 * user can do); devices only say "next", "back", "select", "activate" or "acknowledge". Pausing
 * tracking here also pauses every device and is always available.
 */
export class IntentController {
  private targets: Target[] = [];
  private index = -1;
  private focusedAt = 0;
  private devices: InputDevice[] = [];
  private paused = false;
  readonly log: ActionRecord[] = [];

  constructor(private readonly opts: ControllerOptions = {}) {}

  private now(): number {
    return this.opts.now?.() ?? Date.now();
  }

  setTargets(targets: Target[]): void {
    const focusedId = this.focused?.id;
    this.targets = targets;
    const keep = focusedId ? targets.findIndex((t) => t.id === focusedId) : -1;
    this.index = keep;
    if (keep < 0 && this.focused === undefined) this.opts.onFocus?.(undefined);
  }

  get focused(): Target | undefined {
    return this.targets[this.index];
  }

  get isPaused(): boolean {
    return this.paused;
  }

  attach(device: InputDevice): void {
    this.devices.push(device);
    if (!device.available) return;
    device.pause(this.paused);
    device.start((e) => this.handle(e));
  }

  detachAll(): void {
    for (const d of this.devices) d.stop();
    this.devices = [];
  }

  /** Pause or resume all tracking. Always available, from any state. */
  setPaused(paused: boolean): void {
    this.paused = paused;
    for (const d of this.devices) d.pause(paused);
  }

  private focusAt(i: number): void {
    if (this.targets.length === 0) return;
    this.index = ((i % this.targets.length) + this.targets.length) % this.targets.length;
    this.focusedAt = this.now();
    this.opts.onFocus?.(this.focused);
  }

  focusById(id: string): boolean {
    const i = this.targets.findIndex((t) => t.id === id);
    if (i < 0) return false;
    this.focusAt(i);
    return true;
  }

  /** Handle one intent. Returns true if it caused an action. */
  handle(e: IntentEvent): boolean {
    if (this.paused) return false;
    if (this.opts.refreshTargets) this.setTargets(this.opts.refreshTargets());
    if (this.targets.length === 0) return false;
    switch (e.intent) {
      case 'next':
        this.focusAt(this.index + 1);
        return false;
      case 'back':
        this.focusAt(this.index < 0 ? -1 : this.index - 1);
        return false;
      case 'select':
        if (e.targetId) this.focusById(e.targetId);
        return false;
      case 'acknowledge': {
        const i = this.targets.findIndex((t) => t.kind === 'acknowledge');
        if (i < 0) return false;
        this.focusAt(i);
        return this.run(e);
      }
      case 'activate': {
        if (e.targetId) {
          if (!this.focusById(e.targetId)) return false;
        } else if (this.index < 0) {
          return false;
        } else if (this.now() - this.focusedAt < (this.opts.minFocusMs ?? 0)) {
          return false; // false-activation guard: it was only just focused
        }
        return this.run(e);
      }
    }
  }

  private run(e: IntentEvent): boolean {
    const t = this.focused;
    if (!t) return false;
    const via = `intent:${e.device}`;
    const record: ActionRecord = { targetId: t.id, label: t.label, device: e.device, intent: e.intent, via, at: e.at };
    this.log.push(record);
    this.opts.onAction?.(record);
    void t.activate({ via, device: e.device });
    return true;
  }
}
