import type { Percept, SenseCardCapability, Tier } from '@sense/protocol';
import type { VerificationOutcome } from '@sense/identity';

export interface Freshness {
  /** Age of the oldest timestamp in the data, in seconds. */
  ageSeconds: number;
  maxAgeSeconds: number | null;
  stale: boolean;
}

/**
 * Freshness comes from the payload's own timestamps (measuredAt / asOf), not from when it
 * arrived. Static capabilities (maxAgeSeconds = null) never go stale.
 */
export function assessFreshness(
  capability: Pick<SenseCardCapability, 'freshness'> | undefined,
  timestamps: string[],
  nowMs: number,
): Freshness {
  const max = capability?.freshness.maxAgeSeconds ?? null;
  const times = timestamps.map((t) => Date.parse(t)).filter((t) => !Number.isNaN(t));
  const oldest = times.length > 0 ? Math.min(...times) : nowMs;
  const age = Math.max(0, Math.round((nowMs - oldest) / 1000));
  return { ageSeconds: age, maxAgeSeconds: max, stale: max !== null && age > max };
}

/** VERIFIED identity + stale data is UNVERIFIED. Stale data is never shown as VERIFIED. */
export function tierFor(outcome: VerificationOutcome, freshness: Freshness): Tier {
  if (outcome === 'REJECTED') return 'REJECTED';
  if (outcome === 'UNVERIFIED') return 'UNVERIFIED';
  return freshness.stale ? 'UNVERIFIED' : 'VERIFIED';
}

const TIER_RANK: Record<Tier, number> = { VERIFIED: 0, INFERRED: 1, UNVERIFIED: 2, REJECTED: 3 };

export function tierRank(t: Tier): number {
  return TIER_RANK[t];
}

export interface Claim {
  /** What the claim is about, e.g. `alarm:fire-1` or `allergen:peanut`. */
  topic: string;
  /** What it says, e.g. `active` or `absent`. */
  value: string;
  percept: Percept;
}

export interface Conflict {
  topic: string;
  leader: Claim;
  others: Claim[];
  message: string;
}

const byTrust = (a: Claim, b: Claim): number =>
  tierRank(a.percept.provenance.tier) - tierRank(b.percept.provenance.tier) ||
  b.percept.urgency - a.percept.urgency ||
  b.percept.timestamp.localeCompare(a.percept.timestamp);

/**
 * Conflict policy: when sources disagree, show both, order by tier, and never silently pick one.
 * When a VERIFIED source disagrees with an INFERRED one, the verified one leads and the
 * disagreement is stated. No claim is ever dropped.
 */
export function reconcileClaims(claims: Claim[]): { ordered: Claim[]; conflicts: Conflict[] } {
  const ordered = [...claims].sort(byTrust);
  const conflicts: Conflict[] = [];
  const topics = new Set(claims.map((c) => c.topic));
  for (const topic of topics) {
    const group = ordered.filter((c) => c.topic === topic);
    const values = new Set(group.map((c) => c.value));
    if (values.size < 2) continue;
    const [leader, ...others] = group as [Claim, ...Claim[]];
    const disagreeing = others.filter((o) => o.value !== leader.value);
    if (disagreeing.length === 0) continue;
    const who = (c: Claim) =>
      `${c.percept.provenance.sourceLabel ?? c.percept.provenance.source} (${c.percept.provenance.tier.toLowerCase()}) says ${c.value}`;
    conflicts.push({
      topic,
      leader,
      others: disagreeing,
      message: `Sources disagree about ${topic.replace(':', ' ')}: ${who(leader)}; ${disagreeing.map(who).join('; ')}. The higher-trust source is shown first; nothing was discarded.`,
    });
  }
  return { ordered, conflicts };
}
