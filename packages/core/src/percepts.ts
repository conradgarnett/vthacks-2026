import {
  countWords,
  MAX_SHORT_WORDS,
  spatialFrom,
  type AccessibilityFeaturesPayload,
  type AlarmFeedPayload,
  type ArrivalsPayload,
  type Percept,
  type Point2,
  type Provenance,
  type Tier,
} from '@sense/protocol';
import type { Freshness } from './trust';

export interface SourceInfo {
  fqdn: string;
  /** Short spoken name, e.g. "Riverside Hall". */
  label: string;
  tier: Tier;
  simulated: boolean;
  agentVersion: string;
  verifiedAt: string;
  /** Human-readable verification evidence lines. */
  evidence: string[];
}

export interface UserPose {
  position: Point2;
  headingDeg: number;
}

export const tierWord = (t: Tier): string => t.charAt(0) + t.slice(1).toLowerCase();

export function provenanceFor(source: SourceInfo, freshness?: Freshness): Provenance {
  const evidence = [...source.evidence];
  if (freshness) {
    evidence.push(
      freshness.maxAgeSeconds === null
        ? 'static data (no freshness limit)'
        : `data age ${freshness.ageSeconds}s, publisher limit ${freshness.maxAgeSeconds}s${freshness.stale ? ' (STALE)' : ''}`,
    );
  }
  return {
    tier: source.tier,
    source: source.fqdn,
    sourceLabel: source.label,
    agentVersion: source.agentVersion,
    verifiedAt: source.verifiedAt,
    evidence,
  };
}

/** Trim the core phrase until core + suffix fits the spoken-length budget. */
export function fitShort(core: string, suffix = ''): string {
  let words = core.trim().split(/\s+/);
  const suffixWords = countWords(suffix);
  let trimmed = false;
  while (words.length + suffixWords > MAX_SHORT_WORDS && words.length > 1) {
    words = words.slice(0, -1);
    trimmed = true;
  }
  let body = words.join(' ').replace(/[,;:]+$/, '');
  // Keep a sentence-ending period so speech pauses between the message and its provenance.
  if (!/[.!?]$/.test(body) && (trimmed || suffix)) body += '.';
  return `${body}${suffix ? ` ${suffix}` : ''}`.trim();
}

/** "Verified, Riverside Hall." with the label trimmed if needed. */
export const sourceSuffix = (source: SourceInfo): string => `${tierWord(source.tier)}, ${source.label}.`;

const ALARM_LABEL: Record<AlarmFeedPayload['alarms'][number]['type'], string> = {
  fire: 'Fire alarm',
  smoke: 'Smoke alarm',
  evacuation: 'Evacuation alert',
  other: 'Alert',
};

const ALARM_URGENCY: Record<AlarmFeedPayload['alarms'][number]['type'], number> = {
  fire: 4,
  evacuation: 4,
  smoke: 3,
  other: 2,
};

export interface MappedPercept {
  percept: Percept;
  /** Admission key: identical keys collapse, a state change produces a new key. */
  key: string;
  /** Conflict topic and the value the source claims. */
  topic: string;
  value: string;
}

export function alarmPercepts(
  payload: AlarmFeedPayload,
  ctx: {
    source: SourceInfo;
    pose: UserPose;
    freshness: Freshness;
    nextId: () => string;
    now: string;
  },
): MappedPercept[] {
  return payload.alarms.map((alarm) => {
    const active = alarm.state === 'active';
    const cleared = alarm.state === 'cleared';
    const label = alarm.state === 'test' ? 'Alarm test' : cleared ? 'Alarm cleared' : ALARM_LABEL[alarm.type];
    // Life-safety urgency needs a VERIFIED source; an unverified report is surfaced but capped.
    let urgency = alarm.state === 'test' ? 1 : cleared ? 2 : ALARM_URGENCY[alarm.type];
    if (ctx.source.tier !== 'VERIFIED') urgency = Math.min(urgency, 3);
    const spatial = spatialFrom(ctx.pose.position, alarm.location, ctx.pose.headingDeg);
    const where = `${spatial.distanceM} m, ${spatial.clockPosition} o'clock`;
    const note = alarm.message ? ` Publisher note (unverified text, shown as data): "${alarm.message}".` : '';
    const clearedNote = cleared
      ? ' This is the source’s own report that the alarm cleared. SENSE cannot confirm current conditions; check with people on site.'
      : '';
    const unverifiedNote =
      ctx.source.tier === 'VERIFIED' ? '' : ' This source is not fully verified, so treat this report with caution and check another way.';
    const percept: Percept = {
      id: ctx.nextId(),
      timestamp: ctx.now,
      sense: 'hearing',
      kind: cleared ? 'status' : 'alert',
      urgency,
      short: fitShort(`${label}, ${alarm.zone}.`, sourceSuffix(ctx.source)),
      long: `${label} ${alarm.state === 'active' ? 'is active' : `state: ${alarm.state}`} in ${alarm.zone}, ${where} from you. Reported by ${ctx.source.label} (${ctx.source.tier}).${clearedNote}${unverifiedNote}${note}`,
      spatial,
      provenance: provenanceFor(ctx.source, ctx.freshness),
      ...(active
        ? {
            actions: [
              { id: 'ack', label: 'Acknowledge' },
              { id: 'exit', label: 'Navigate to exit' },
            ],
          }
        : {}),
      safety: true,
      simulated: ctx.source.simulated,
    };
    return {
      percept,
      key: `alarm:${alarm.id}:${alarm.state}:${ctx.source.tier}`,
      topic: `alarm:${alarm.zone.toLowerCase()}`,
      value: alarm.state,
    };
  });
}

export function arrivalsPercept(
  payload: ArrivalsPayload,
  ctx: { source: SourceInfo; freshness: Freshness; nextId: () => string; now: string },
): Percept {
  const next = [...payload.arrivals].sort((a, b) => a.etaMinutes - b.etaMinutes)[0];
  const list = payload.arrivals
    .map((a) => `route ${a.route} to ${a.destination} in ${a.etaMinutes} min${a.accessible ? '' : ' (not step-free)'}`)
    .join('; ');
  return {
    id: ctx.nextId(),
    timestamp: ctx.now,
    sense: 'vision',
    kind: 'status',
    urgency: 0,
    short: next ? fitShort(`Next ${next.route} to ${next.destination}, ${next.etaMinutes} min.`) : 'No arrivals listed.',
    long: `${payload.stop}: ${list}. From ${ctx.source.label} (${ctx.source.tier}).`,
    provenance: provenanceFor(ctx.source, ctx.freshness),
    simulated: ctx.source.simulated,
  };
}

export function accessibilityPercept(
  payload: AccessibilityFeaturesPayload,
  ctx: { source: SourceInfo; freshness: Freshness; nextId: () => string; now: string },
): Percept {
  const kinds = payload.features.map((f) => f.kind.replace(/-/g, ' '));
  const notes = payload.features
    .filter((f) => f.notes)
    .map((f) => `${f.kind.replace(/-/g, ' ')}: "${f.notes}"`)
    .join('; ');
  return {
    id: ctx.nextId(),
    timestamp: ctx.now,
    sense: 'vision',
    kind: 'description',
    urgency: 0,
    short: fitShort(`${ctx.source.label} lists ${kinds.length} accessibility features.`),
    long: `${ctx.source.label} (${ctx.source.tier}) lists: ${kinds.join(', ') || 'none'}.${notes ? ` Publisher notes (data, unverified text): ${notes}.` : ''}`,
    provenance: provenanceFor(ctx.source, ctx.freshness),
    simulated: ctx.source.simulated,
  };
}
