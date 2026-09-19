import {
  guardPercept,
  iso,
  spatialFrom,
  type Clock,
  type Percept,
  type Point2,
  type ScentStatusDto,
  type SenseModule,
  type Urgency,
} from '@sense/protocol';
import { assess, type Assessment, type ScentAlarm, type ScentSource } from './engine';
import { COMBINATION_RULES, LEVEL_LABEL, RULES } from './rules';

export interface ScentDeps {
  clock: Clock;
  nextId: () => string;
  getPose: () => { position: Point2; headingDeg: number };
}

const tierWord = (t: string) => t.charAt(0) + t.slice(1).toLowerCase();

/** Level -> urgency. Only a VERIFIED level-4 result is life-safety urgency 4. */
export function urgencyFor(level: number, tier: string): Urgency {
  if (level <= 0) return 1;
  if (level === 1) return 2;
  if (level === 2 || level === 3) return 3;
  return tier === 'VERIFIED' ? 4 : 3;
}

/**
 * ScentGuard (smell): keeps state across updates and emits a percept when the risk level, its
 * trust tier, or whether current conditions are known, changes. Each percept explains which rules
 * fired, on which readings, how fresh they are, and who reported them.
 */
export class ScentGuard {
  private last: Assessment | undefined;
  private lastKey = '';

  constructor(private readonly deps: ScentDeps) {}

  evaluate(input: { sources: ScentSource[]; alarms: ScentAlarm[] }): Assessment {
    return assess({ ...input, nowMs: this.deps.clock.now() });
  }

  /** Re-evaluate and return percepts for anything that changed since the last call. */
  update(input: { sources: ScentSource[]; alarms: ScentAlarm[] }): Percept[] {
    const a = this.evaluate(input);
    const hasData = input.sources.some((s) => s.readings.length > 0) || input.alarms.some((x) => x.active);
    const key = `${a.level}|${a.tier}|${a.currentUnknown}|${hasData}`;
    this.last = a;
    if (key === this.lastKey) return [];
    const previous = this.lastKey;
    this.lastKey = key;
    if (!hasData && previous === '') return []; // nothing to say before any data has ever arrived
    return [this.percept(a, previous)];
  }

  status(): ScentStatusDto | undefined {
    const a = this.last;
    if (!a) return undefined;
    return {
      level: a.level,
      summary: this.summary(a),
      tier: a.tier === 'REJECTED' ? 'UNVERIFIED' : a.tier,
      rules: [...a.hits.filter((h) => h.rule.scope === 'building').map((h) => h.rule.id), ...a.combos],
    };
  }

  private summary(a: Assessment): string {
    if (a.level === 0)
      return a.currentUnknown
        ? 'Current conditions unknown: no fresh verified building readings.'
        : 'No elevated reading reported by fresh verified building sensors. This is not a safety guarantee.';
    return `Level ${a.level} (${LEVEL_LABEL[a.level]})${a.currentUnknown ? ', last known, not current' : ''}.`;
  }

  private percept(a: Assessment, previousKey: string): Percept {
    const src = a.driver;
    const label = src?.label ?? 'air sensors';
    const tier = a.tier === 'REJECTED' ? 'UNVERIFIED' : a.tier;
    const urgency = urgencyFor(a.level, tier);
    const buildingHits = a.hits.filter((h) => h.rule.scope === 'building').sort((x, y) => y.rule.level - x.rule.level);
    const top = buildingHits[0];
    const prevLevel = Number(previousKey.split('|')[0]);
    const direction =
      previousKey === '' || Number.isNaN(prevLevel) ? '' : a.level > prevLevel ? 'rising' : a.level < prevLevel ? 'reduced' : '';

    let core: string;
    let kind: Percept['kind'];
    if (a.level === 0) {
      kind = 'status';
      core = a.currentUnknown ? 'Air conditions unknown.' : 'No elevated air reading reported.';
    } else {
      kind = a.level >= 2 ? 'alert' : 'status';
      core = `Smoke risk level ${a.level}${a.currentUnknown ? ', last known' : direction === 'reduced' ? ', reduced' : ''}.`;
    }
    const suffix = `${tierWord(tier)}, ${label}.`;
    const short = trim(core, suffix);

    const lines: string[] = [];
    if (a.level > 0) lines.push(`Level ${a.level} of 4 (${LEVEL_LABEL[a.level]})${direction ? `, ${direction}` : ''}.`);
    for (const h of buildingHits.slice(0, 4)) {
      lines.push(
        `${h.sourceLabel} ${h.reading.label ?? h.reading.kind}: ${h.reading.value} ${h.reading.unit}, ${h.ageSeconds}s old, fires rule ${h.rule.id} (${h.rule.meaning.toLowerCase()}, threshold ${h.rule.min} ${h.rule.unit}).`,
      );
    }
    for (const c of a.combos) lines.push(`Combination rule ${c}: ${COMBINATION_RULES.find((r) => r.id === c)?.text ?? ''}`);
    if (a.level === 0 && !a.currentUnknown) lines.push('No building reading reached the lowest threshold in the latest fresh data.');
    lines.push(...a.cautions);
    lines.push(...a.context);
    lines.push(
      a.currentUnknown
        ? 'SENSE does not assume conditions have improved when data is missing or old.'
        : 'This is a data-based risk estimate from the sensors listed, not a measurement of the air where you are and not a safety guarantee.',
    );
    lines.push(`Thresholds are illustrative, not from a standard (${RULES.length} single-sensor rules).`);

    const spatialSource = top?.reading.location;
    const pose = this.deps.getPose();
    return guardPercept({
      id: this.deps.nextId(),
      timestamp: iso(this.deps.clock),
      sense: 'smell',
      kind,
      urgency,
      short,
      long: lines.join(' '),
      ...(spatialSource ? { spatial: spatialFrom(pose.position, spatialSource, pose.headingDeg) } : {}),
      provenance: {
        tier,
        source: src?.fqdn ?? 'scentguard',
        sourceLabel: label,
        ...(src?.agentVersion ? { agentVersion: src.agentVersion } : {}),
        ...(src?.verifiedAt ? { verifiedAt: src.verifiedAt } : {}),
        evidence: [
          ...(src?.evidence ?? []),
          ...buildingHits
            .slice(0, 3)
            .map(
              (h) =>
                `rule ${h.rule.id}: ${h.reading.value} ${h.reading.unit} >= ${h.rule.min}, age ${h.ageSeconds}s${h.stale ? ' (STALE)' : ''}`,
            ),
          ...a.combos.map((c) => `combination rule ${c}`),
        ],
      },
      safety: true,
      ...(src?.simulated ? { simulated: true } : {}),
      ...(a.level >= 2 ? { actions: [{ id: 'ack', label: 'Acknowledge' }] } : {}),
    });
  }

  module(updates: () => AsyncIterable<{ sources: ScentSource[]; alarms: ScentAlarm[] }>): SenseModule {
    return {
      id: 'smell',
      inputs: ['verified air-quality feeds', 'verified alarm-feed', 'regional air data'],
      produce: async function* (this: ScentGuard) {
        for await (const u of updates()) yield* this.update(u);
      }.bind(this),
    };
  }
}

function trim(core: string, suffix: string): string {
  let w = core.trim().split(/\s+/);
  const sw = suffix.split(/\s+/).length;
  while (w.length + sw > 10 && w.length > 1) w = w.slice(0, -1);
  return `${w
    .join(' ')
    .replace(/[,;:]+$/, '')
    .replace(/\.?$/, '.')} ${suffix}`;
}
