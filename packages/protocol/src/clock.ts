/** Injected time so tests and the demo timeline are deterministic. */
export interface Clock {
  /** Milliseconds since the Unix epoch. */
  now(): number;
}

export const systemClock: Clock = { now: () => Date.now() };

export class ManualClock implements Clock {
  constructor(private t: number = Date.parse('2026-01-15T10:00:00.000Z')) {}
  now(): number {
    return this.t;
  }
  set(t: number): void {
    this.t = t;
  }
  advance(ms: number): void {
    this.t += ms;
  }
}

/**
 * The real clock plus an adjustable offset. The live demo uses it so "skip 2 minutes" can show stale
 * data instantly. Anything that skips time is labelled as a SIMULATED TIME SKIP in the UI.
 */
export class OffsetClock implements Clock {
  private offset = 0;
  now(): number {
    return Date.now() + this.offset;
  }
  advance(ms: number): void {
    this.offset += ms;
  }
  get skippedMs(): number {
    return this.offset;
  }
}

export function iso(clock: Clock, offsetMs = 0): string {
  return new Date(clock.now() + offsetMs).toISOString();
}

/** Small seedable PRNG (mulberry32) for deterministic simulations and tests. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Sequential ids ("p-1", "p-2", ...) for deterministic output. */
export function sequentialIds(prefix: string): () => string {
  let n = 0;
  return () => `${prefix}-${++n}`;
}
