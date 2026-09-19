import type { Point2, Tier } from '@sense/protocol';
import { COMBINATION_RULES, REGIONAL_LEVEL_CAP, RULES, type ReadingKind, type Rule } from './rules';

export interface ScentReading {
  sensorId: string;
  kind: ReadingKind;
  value: number;
  unit: string;
  /** ISO time the sensor measured this. Freshness is judged from this, not from arrival time. */
  measuredAt: string;
  label?: string;
  location?: Point2;
}

export interface ScentSource {
  fqdn: string;
  label: string;
  /** Identity tier of the source. */
  tier: Tier;
  simulated: boolean;
  agentVersion: string;
  verifiedAt: string;
  evidence: string[];
  readings: ScentReading[];
  /** Publisher-declared freshness limit for this feed (seconds). */
  maxAgeSeconds: number | null;
}

export interface ScentAlarm {
  fqdn: string;
  label: string;
  tier: Tier;
  active: boolean;
}

export interface RuleHit {
  rule: Rule;
  reading: ScentReading;
  source: string;
  sourceLabel: string;
  ageSeconds: number;
  /** True when this reading is older than the source's declared limit. */
  stale: boolean;
  tier: Tier;
}

export interface Assessment {
  /** 0 means "no elevated reading reported by fresh verified sensors". It is never "safe". */
  level: 0 | 1 | 2 | 3 | 4;
  /** Tier of the evidence that produced this level (stale or unverified evidence is UNVERIFIED). */
  tier: Tier;
  hits: RuleHit[];
  combos: string[];
  /** True when there is no fresh reading from a verified building source: current conditions are unknown. */
  currentUnknown: boolean;
  /** Human reasons for `currentUnknown` and other cautions. */
  cautions: string[];
  /** Oldest reading that drove the result. */
  asOf: string | undefined;
  /** The source whose reading drove the level (for provenance). */
  driver:
    | {
        fqdn: string;
        label: string;
        simulated: boolean;
        agentVersion: string;
        verifiedAt: string;
        evidence: string[];
        maxAgeSeconds: number | null;
      }
    | undefined;
  /** Regional context lines (for example a high city AQI) that never change the level by themselves above the cap. */
  context: string[];
}

const RULES_BY_KIND = new Map<ReadingKind, Rule[]>();
for (const r of RULES)
  RULES_BY_KIND.set(
    r.kind,
    [...(RULES_BY_KIND.get(r.kind) ?? []), r].sort((a, b) => b.min - a.min),
  );

/** The highest rule the reading satisfies, or undefined. */
export function ruleFor(kind: ReadingKind, scope: Rule['scope'], value: number): Rule | undefined {
  return (RULES_BY_KIND.get(kind) ?? []).find((r) => r.scope === scope && value >= r.min);
}

/**
 * Transparent risk engine. Pure function of (sources, alarms, now): the same inputs always give the
 * same result, and every level can be traced to named rules and readings.
 *
 * Honesty rules:
 *  - Missing, stale or unverified data never lowers the level and never produces an all-clear.
 *    Stale data keeps the last known level but is tagged UNVERIFIED and flagged as unknown-now.
 *  - Level 0 only means "no elevated reading was reported by fresh, verified building sensors".
 */
export function assess(input: { sources: ScentSource[]; alarms: ScentAlarm[]; nowMs: number }): Assessment {
  const { sources, alarms, nowMs } = input;
  const hits: RuleHit[] = [];
  const context: string[] = [];
  let freshVerifiedBuilding = 0;
  const cautions: string[] = [];

  for (const s of sources) {
    for (const r of s.readings) {
      const scope: Rule['scope'] = r.kind === 'aqi' || r.kind === 'pm25' ? 'regional' : 'building';
      const measured = Date.parse(r.measuredAt);
      const ageSeconds = Number.isNaN(measured) ? Number.POSITIVE_INFINITY : Math.max(0, Math.round((nowMs - measured) / 1000));
      const stale = s.maxAgeSeconds !== null && ageSeconds > s.maxAgeSeconds;
      if (scope === 'building' && !stale && s.tier === 'VERIFIED') freshVerifiedBuilding++;
      const rule = ruleFor(r.kind, scope, r.value);
      if (!rule) continue;
      const tier: Tier = s.tier === 'VERIFIED' && !stale ? 'VERIFIED' : 'UNVERIFIED';
      hits.push({ rule, reading: r, source: s.fqdn, sourceLabel: s.label, ageSeconds, stale, tier });
    }
  }

  const buildingHits = hits.filter((h) => h.rule.scope === 'building');
  const regionalHits = hits.filter((h) => h.rule.scope === 'regional');
  let level = Math.max(
    0,
    ...buildingHits.map((h) => h.rule.level),
    Math.min(REGIONAL_LEVEL_CAP, Math.max(0, ...regionalHits.map((h) => h.rule.level))),
  ) as Assessment['level'];

  const combos: string[] = [];
  const verifiedFire = alarms.find((a) => a.active && a.tier === 'VERIFIED');
  if (verifiedFire) {
    level = Math.max(level, COMBINATION_RULES[0].level) as Assessment['level'];
    combos.push(COMBINATION_RULES[0].id);
    const s3 = RULES.find((r) => r.id === 'S3');
    if (s3 && buildingHits.some((h) => h.reading.kind === 'smoke' && h.reading.value >= s3.min)) {
      level = 4;
      combos.push(COMBINATION_RULES[1].id);
    }
  }

  for (const h of regionalHits) {
    context.push(
      `${h.sourceLabel} reports ${h.reading.label ?? h.reading.kind} ${h.reading.value} ${h.reading.unit} (rule ${h.rule.id}: ${h.rule.meaning.toLowerCase()}). Regional data does not describe air inside a building, and it does not mean outdoor air is better.`,
    );
  }

  // Which evidence drove the level? The highest-level building hit (ties: freshest).
  const drivers = [...buildingHits].sort((a, b) => b.rule.level - a.rule.level || a.ageSeconds - b.ageSeconds);
  const top = drivers[0];
  // With no rule hit, the statement "no elevated reading" still rests on some source: name the
  // freshest verified building source that supplied readings.
  const isBuilding = (k: ReadingKind) => k !== 'aqi' && k !== 'pm25';
  const supporting = sources
    .filter((s) => s.readings.some((r) => isBuilding(r.kind)))
    .sort((a, b) => Number(b.tier === 'VERIFIED') - Number(a.tier === 'VERIFIED'))[0];
  const driverSource = top ? sources.find((s) => s.fqdn === top.source) : supporting;

  let tier: Tier;
  if (level === 0) tier = freshVerifiedBuilding > 0 ? 'VERIFIED' : 'UNVERIFIED';
  else if (top)
    tier = buildingHits.filter((h) => h.rule.level === top.rule.level).some((h) => h.tier === 'VERIFIED') ? 'VERIFIED' : 'UNVERIFIED';
  else tier = verifiedFire ? 'VERIFIED' : 'UNVERIFIED';

  const currentUnknown = freshVerifiedBuilding === 0;
  if (currentUnknown) {
    cautions.push(
      sources.some((s) => s.readings.length > 0)
        ? 'The building air readings are out of date or from a source that is not fully verified, so current conditions are unknown.'
        : 'No building air-quality data is available from any source, so current conditions are unknown.',
    );
  }
  const stale = buildingHits.filter((h) => h.stale);
  for (const h of stale)
    cautions.push(
      `${h.sourceLabel} ${h.reading.label ?? h.reading.kind} reading is ${h.ageSeconds}s old (limit ${driverSource?.maxAgeSeconds ?? '?'}s): last known, not current.`,
    );

  const oldest = buildingHits.length ? Math.min(...buildingHits.map((h) => Date.parse(h.reading.measuredAt))) : undefined;
  return {
    level,
    tier,
    hits,
    combos,
    currentUnknown,
    cautions,
    asOf: oldest !== undefined && !Number.isNaN(oldest) ? new Date(oldest).toISOString() : undefined,
    driver: driverSource
      ? {
          fqdn: driverSource.fqdn,
          label: driverSource.label,
          simulated: driverSource.simulated,
          agentVersion: driverSource.agentVersion,
          verifiedAt: driverSource.verifiedAt,
          evidence: driverSource.evidence,
          maxAgeSeconds: driverSource.maxAgeSeconds,
        }
      : verifiedFire
        ? {
            fqdn: verifiedFire.fqdn,
            label: verifiedFire.label,
            simulated: sources[0]?.simulated ?? true,
            agentVersion: '',
            verifiedAt: '',
            evidence: [],
            maxAgeSeconds: null,
          }
        : undefined,
    context,
  };
}
