import { DwellSelector, IDENTITY_CALIBRATION, applyCalibration, type Calibration, type DwellEvent, type Rect, type Sample } from './dwell';
import type { InputDevice, Intent, IntentSink, Scheduler } from './types';

abstract class BaseDevice implements InputDevice {
  abstract readonly id: string;
  abstract readonly label: string;
  available = true;
  unavailableReason?: string;
  paused = false;
  protected emit: IntentSink | undefined;
  protected started = false;

  start(emit: IntentSink): void {
    this.emit = emit;
    this.started = true;
    this.onStart();
  }
  stop(): void {
    this.started = false;
    this.onStop();
    this.emit = undefined;
  }
  pause(paused: boolean): void {
    this.paused = paused;
    this.onPause(paused);
  }
  protected onStart(): void {}
  protected onStop(): void {}
  protected onPause(_paused: boolean): void {}
  protected send(intent: Intent, at: number, targetId?: string): void {
    if (this.paused || !this.started || !this.emit) return;
    this.emit({ intent, device: this.id, at, ...(targetId ? { targetId } : {}) });
  }
}

/**
 * One-switch auto-scan: focus advances by itself every `intervalMs` (a "next" intent) and a single
 * switch press selects whatever is focused (an "activate" intent). It needs no camera, no pointer
 * and only one reliable movement, which makes it the most robust fallback.
 */
export class SwitchScanDriver extends BaseDevice {
  readonly id = 'switch-scan';
  readonly label = 'One-switch scanning';
  private stopTicks: (() => void) | undefined;

  constructor(
    private readonly scheduler: Scheduler,
    public intervalMs = 1500,
  ) {
    super();
  }

  setIntervalMs(ms: number): void {
    this.intervalMs = Math.min(10_000, Math.max(300, ms));
    if (this.started && !this.paused) this.restartTicks();
  }

  /** The user pressed the switch: select the focused item. */
  press(): void {
    this.send('activate', this.scheduler.now());
  }

  /** A long press goes back one item. */
  pressLong(): void {
    this.send('back', this.scheduler.now());
  }

  private restartTicks(): void {
    this.stopTicks?.();
    this.stopTicks = this.scheduler.setInterval(() => this.send('next', this.scheduler.now()), this.intervalMs);
  }

  protected override onStart(): void {
    if (!this.paused) this.restartTicks();
  }
  protected override onStop(): void {
    this.stopTicks?.();
    this.stopTicks = undefined;
  }
  protected override onPause(paused: boolean): void {
    if (!this.started) return;
    if (paused) {
      this.stopTicks?.();
      this.stopTicks = undefined;
    } else this.restartTicks();
  }
}

/** Where pointer samples come from: eye gaze, head pose, a hand landmark, or a stand-in. */
export interface PointerSource {
  readonly available: boolean;
  readonly unavailableReason?: string;
}

/**
 * Dwell-to-select over a pointer stream (gaze, head or hand). Includes tremor smoothing, an
 * adjustable dwell time, calibration, and a false-activation guard. Samples are fed with `feed()`.
 */
export class DwellGazeDriver extends BaseDevice {
  readonly id = 'dwell-gaze';
  readonly label = 'Gaze, head or hand dwell';
  readonly selector: DwellSelector;
  calibration: Calibration = IDENTITY_CALIBRATION;
  /** Latest dwell events, for drawing a progress ring. */
  onDwell: ((e: DwellEvent) => void) | undefined;

  constructor(opts: { dwellMs?: number; leaveMarginPx?: number; refractoryMs?: number } = {}) {
    super();
    this.selector = new DwellSelector(opts.dwellMs ?? 1000, opts.leaveMarginPx ?? 12, opts.refractoryMs ?? 1500);
  }

  setTargets(rects: Rect[]): void {
    this.selector.setTargets(rects);
  }
  setDwellMs(ms: number): void {
    this.selector.setDwellMs(ms);
  }
  setCalibration(c: Calibration): void {
    this.calibration = c;
  }

  /** Feed one raw pointer sample. Ignored while paused. */
  feed(raw: Sample): void {
    if (this.paused || !this.started) return;
    const p = applyCalibration(this.calibration, raw);
    for (const e of this.selector.update({ ...p, t: raw.t })) {
      this.onDwell?.(e);
      if (e.type === 'enter') this.send('select', raw.t, e.id);
      if (e.type === 'activate') this.send('activate', raw.t, e.id);
    }
  }

  protected override onPause(paused: boolean): void {
    if (paused) this.selector.reset();
  }
}

/** Keyboard mapped to intents, for users who can press a few keys but not navigate normally. */
export interface KeyEventLike {
  key: string;
  preventDefault(): void;
  stopPropagation(): void;
}
export interface KeyTarget {
  addEventListener(type: 'keydown', fn: (e: KeyEventLike) => void, capture?: boolean): void;
  removeEventListener(type: 'keydown', fn: (e: KeyEventLike) => void, capture?: boolean): void;
}

export const KEY_INTENTS: Record<string, Intent> = {
  ArrowRight: 'next',
  ArrowDown: 'next',
  n: 'next',
  ArrowLeft: 'back',
  ArrowUp: 'back',
  p: 'back',
  Enter: 'activate',
  ' ': 'activate',
  a: 'acknowledge',
};

export class KeyboardDriver extends BaseDevice {
  readonly id = 'keyboard';
  readonly label = 'Keyboard intents';
  private readonly handler = (e: KeyEventLike) => {
    const intent = KEY_INTENTS[e.key];
    if (!intent || this.paused) return;
    // Intercept so the browser's own handling does not also fire (no double activation).
    e.preventDefault();
    e.stopPropagation();
    this.send(intent, this.scheduler.now());
  };

  constructor(
    private readonly target: KeyTarget,
    private readonly scheduler: Scheduler,
  ) {
    super();
  }

  protected override onStart(): void {
    this.target.addEventListener('keydown', this.handler, true);
  }
  protected override onStop(): void {
    this.target.removeEventListener('keydown', this.handler, true);
  }
}

export interface ScriptStep {
  afterMs: number;
  intent: Intent;
  targetId?: string;
}

/** A scripted device for demos and headless verification: plays a fixed sequence of intents. */
export class ScriptedDriver extends BaseDevice {
  readonly id = 'scripted';
  readonly label = 'Scripted gaze/switch (demo)';
  private cancels: (() => void)[] = [];

  constructor(
    private readonly steps: ScriptStep[],
    private readonly scheduler: Scheduler,
  ) {
    super();
  }

  protected override onStart(): void {
    let at = 0;
    for (const s of this.steps) {
      at += s.afterMs;
      this.cancels.push(this.scheduler.setTimeout(() => this.send(s.intent, this.scheduler.now(), s.targetId), at));
    }
  }
  protected override onStop(): void {
    for (const c of this.cancels) c();
    this.cancels = [];
  }
}

/**
 * Webcam head/eye/hand tracking seam. Real tracking needs a landmark model (for example MediaPipe
 * Tasks) whose model files were NOT vendored in this build, so this device reports itself as
 * unavailable with the reason and never emits anything. Switch scanning, the keyboard and the
 * scripted driver are the working alternatives. A landmark-based `PointerSource` can be plugged
 * into `DwellGazeDriver.feed()` without any other change.
 */
export class WebcamHeadDriver extends BaseDevice {
  readonly id = 'webcam-head';
  readonly label = 'Webcam head/eye/hand tracking';

  constructor(landmarker?: { ready: boolean }) {
    super();
    this.available = Boolean(landmarker?.ready);
    if (!this.available) {
      this.unavailableReason =
        'No landmark model is loaded: MediaPipe Tasks model files are not vendored in this build. Use switch scanning, the keyboard or the scripted driver.';
    }
  }

  protected override onStart(): void {
    if (!this.available) return; // never fake tracking
  }
}
