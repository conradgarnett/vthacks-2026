import type { Clock, Tier } from '@sense/protocol';

export type Admission =
  | { admit: true; key: string }
  | { admit: false; reason: 'duplicate'; key: string; count: number }
  | { admit: false; reason: 'rate-limited'; key: string };

interface Bucket {
  tokens: number;
  updated: number;
}

/**
 * Rate limiting and duplicate collapsing for percepts.
 *  - Identical percepts (same source + key) collapse into a counter instead of re-announcing.
 *  - Each source has a token bucket for distinct percepts.
 *  - INVARIANT: a distinct percept with urgency >= 3 from a VERIFIED source is always admitted,
 *    however loud the source is. (Identical repeats still collapse, so a heartbeat is not re-announced.)
 */
export class RateLimiter {
  private readonly buckets = new Map<string, Bucket>();
  private readonly seen = new Map<string, { at: number; count: number }>();

  constructor(
    private readonly clock: Clock,
    private readonly opts = {
      capacity: 8,
      refillPerSecond: 1,
      duplicateWindowMs: 30_000,
      criticalDuplicateWindowMs: 300_000,
    },
  ) {}

  admit(input: { source: string; key: string; urgency: number; tier: Tier }): Admission {
    const now = this.clock.now();
    const critical = input.tier === 'VERIFIED' && input.urgency >= 3;
    const dedupeKey = `${input.source}|${input.key}`;
    const window = critical ? this.opts.criticalDuplicateWindowMs : this.opts.duplicateWindowMs;

    const prior = this.seen.get(dedupeKey);
    if (prior && now - prior.at < window) {
      prior.count += 1;
      return { admit: false, reason: 'duplicate', key: dedupeKey, count: prior.count };
    }

    const bucket = this.buckets.get(input.source) ?? { tokens: this.opts.capacity, updated: now };
    bucket.tokens = Math.min(
      this.opts.capacity,
      bucket.tokens + ((now - bucket.updated) / 1000) * this.opts.refillPerSecond,
    );
    bucket.updated = now;
    this.buckets.set(input.source, bucket);

    if (bucket.tokens < 1 && !critical) {
      return { admit: false, reason: 'rate-limited', key: dedupeKey };
    }
    bucket.tokens = Math.max(0, bucket.tokens - 1);
    this.seen.set(dedupeKey, { at: now, count: 1 });
    this.prune(now);
    return { admit: true, key: dedupeKey };
  }

  /** Forget an identical-percept record so a state change re-announces. */
  reset(source: string, key: string): void {
    this.seen.delete(`${source}|${key}`);
  }

  private prune(now: number): void {
    if (this.seen.size < 2000) return;
    for (const [k, v] of this.seen) {
      if (now - v.at > this.opts.criticalDuplicateWindowMs) this.seen.delete(k);
    }
  }
}
