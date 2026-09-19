export interface Point {
  x: number;
  y: number;
}

export interface Sample extends Point {
  /** Milliseconds. */
  t: number;
}

export interface Rect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * Tremor smoothing: an exponential moving average plus a dead band. Small involuntary movements
 * inside the dead band are ignored entirely; larger deliberate movements are followed smoothly.
 */
export class TremorFilter {
  private cur: Point | undefined;

  constructor(
    /** 0 < alpha <= 1. Lower is smoother and slower. */
    public alpha = 0.35,
    /** Movements smaller than this (in pixels) are treated as tremor and ignored. */
    public deadbandPx = 6,
  ) {}

  update(p: Point): Point {
    if (!this.cur) {
      this.cur = { ...p };
      return this.cur;
    }
    if (Math.hypot(p.x - this.cur.x, p.y - this.cur.y) <= this.deadbandPx) return this.cur;
    this.cur = { x: this.cur.x + this.alpha * (p.x - this.cur.x), y: this.cur.y + this.alpha * (p.y - this.cur.y) };
    return this.cur;
  }

  reset(): void {
    this.cur = undefined;
  }
}

export type DwellEvent =
  | { type: 'enter'; id: string }
  | { type: 'leave'; id: string }
  | { type: 'progress'; id: string; fraction: number }
  | { type: 'activate'; id: string };

const inside = (r: Rect, p: Point, margin = 0) =>
  p.x >= r.x - margin && p.x <= r.x + r.w + margin && p.y >= r.y - margin && p.y <= r.y + r.h + margin;

/**
 * Dwell-to-select state machine. Holding the (smoothed) pointer on one target for `dwellMs`
 * activates it. Guards against accidental activation:
 *  - a quick pass over a target never activates;
 *  - once inside, the pointer may drift `leaveMarginPx` beyond the edge without losing the dwell;
 *  - after an activation the same target cannot fire again until the pointer leaves it AND the
 *    refractory period has passed.
 */
export class DwellSelector {
  private targets: Rect[] = [];
  private current: string | undefined;
  private enteredAt = 0;
  private lastActivation = new Map<string, number>();
  private fired = false;

  constructor(
    public dwellMs = 1000,
    public leaveMarginPx = 12,
    public refractoryMs = 1500,
    private readonly filter = new TremorFilter(),
  ) {}

  setTargets(rects: Rect[]): void {
    this.targets = rects;
    if (this.current && !rects.some((r) => r.id === this.current)) this.current = undefined;
  }

  setDwellMs(ms: number): void {
    this.dwellMs = Math.min(5000, Math.max(200, ms));
  }

  reset(): void {
    this.current = undefined;
    this.fired = false;
    this.filter.reset();
  }

  update(sample: Sample): DwellEvent[] {
    const p = this.filter.update(sample);
    const events: DwellEvent[] = [];
    const held = this.current ? this.targets.find((r) => r.id === this.current) : undefined;
    // Hysteresis: stay on the current target while within the leave margin.
    const hit = held && inside(held, p, this.leaveMarginPx) ? held : this.targets.find((r) => inside(r, p));

    if (hit?.id !== this.current) {
      if (this.current) events.push({ type: 'leave', id: this.current });
      this.current = hit?.id;
      this.enteredAt = sample.t;
      this.fired = false;
      if (hit) events.push({ type: 'enter', id: hit.id });
      return events;
    }
    if (!hit || this.fired) return events;

    const elapsed = sample.t - this.enteredAt;
    events.push({ type: 'progress', id: hit.id, fraction: Math.min(1, elapsed / this.dwellMs) });
    const last = this.lastActivation.get(hit.id);
    const refractory = last !== undefined && sample.t - last < this.refractoryMs;
    if (elapsed >= this.dwellMs && !refractory) {
      this.fired = true;
      this.lastActivation.set(hit.id, sample.t);
      events.push({ type: 'activate', id: hit.id });
    }
    return events;
  }
}

/** Least-squares linear calibration per axis: target = gain * measured + offset. */
export interface Calibration {
  gainX: number;
  offsetX: number;
  gainY: number;
  offsetY: number;
}

export const IDENTITY_CALIBRATION: Calibration = { gainX: 1, offsetX: 0, gainY: 1, offsetY: 0 };

function fit(measured: number[], target: number[]): [number, number] {
  const n = measured.length;
  const mx = measured.reduce((a, b) => a + b, 0) / n;
  const ty = target.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i++) {
    num += ((measured[i] as number) - mx) * ((target[i] as number) - ty);
    den += ((measured[i] as number) - mx) ** 2;
  }
  if (den < 1e-9) throw new Error('calibration points must differ on both axes');
  const gain = num / den;
  return [gain, ty - gain * mx];
}

/** Fit a calibration from at least three (measured, target) pairs, e.g. from the calibration screen. */
export function fitCalibration(pairs: { measured: Point; target: Point }[]): Calibration {
  if (pairs.length < 3) throw new Error('at least three calibration points are needed');
  const [gainX, offsetX] = fit(
    pairs.map((p) => p.measured.x),
    pairs.map((p) => p.target.x),
  );
  const [gainY, offsetY] = fit(
    pairs.map((p) => p.measured.y),
    pairs.map((p) => p.target.y),
  );
  return { gainX, offsetX, gainY, offsetY };
}

export const applyCalibration = (c: Calibration, p: Point): Point => ({ x: c.gainX * p.x + c.offsetX, y: c.gainY * p.y + c.offsetY });
