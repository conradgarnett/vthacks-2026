import { describeDirection, guardPercept, iso, type Clock, type Percept, type SenseModule, type SensoryProfile } from '@sense/protocol';
import type { SoundEvent, SoundLabel } from '@sense/providers';

const LABEL_TEXT: Record<SoundLabel, string> = {
  siren: 'Siren-like sound',
  knock: 'Knock-like sound',
  doorbell: 'Doorbell-like sound',
  alarm: 'Beeping alarm-like sound',
  speech: 'Speech-like sound',
  vehicle: 'Vehicle-like sound',
  'dog-bark': 'Dog-bark-like sound',
  'glass-break': 'Glass-break-like sound',
  unknown: 'Unrecognised sound',
};

const BASE_URGENCY: Record<SoundLabel, number> = {
  siren: 3,
  alarm: 3,
  'glass-break': 3,
  knock: 2,
  doorbell: 2,
  vehicle: 1,
  'dog-bark': 1,
  speech: 1,
  unknown: 0,
};

/** Sounds that can affect physical safety. They carry provenance in the primary message. */
const SAFETY_LABELS = new Set<SoundLabel>(['siren', 'alarm', 'glass-break']);

export interface EchoDeps {
  clock: Clock;
  nextId: () => string;
  getProfile: () => SensoryProfile;
  /** A currently ACTIVE alarm from a VERIFIED source, if any (supplied from the broker). */
  verifiedAlarm: () => { label: string; fqdn: string } | undefined;
}

/**
 * Echo (hearing): turns sound events into percepts. Sound labels are INFERENCES and always show
 * their confidence. Inferred sounds never exceed urgency 3, and while a VERIFIED building alarm is
 * active they are demoted to a corroborating note: the verified source dominates.
 */
export class EchoSense {
  private readonly lastByLabel = new Map<SoundLabel, number>();

  constructor(private readonly deps: EchoDeps) {}

  /** Urgency for a sound, personalised by the profile. Capped at 3: only a verified source can be 4. */
  priority(e: SoundEvent): number {
    let u = BASE_URGENCY[e.label];
    const p = this.deps.getProfile();
    if (p.translate.hearing && (e.label === 'knock' || e.label === 'doorbell')) u += 1; // a deaf user would otherwise miss it
    if (e.confidence < 0.4) u = Math.min(u, 1); // low confidence stays ambient
    return Math.min(3, u);
  }

  /** Returns a percept, or null when it is a repeat within a few seconds. */
  ingest(e: SoundEvent): Percept | null {
    const now = this.deps.clock.now();
    const last = this.lastByLabel.get(e.label);
    if (last !== undefined && now - last < 4000) return null;
    this.lastByLabel.set(e.label, now);

    const alarm = this.deps.verifiedAlarm();
    const safety = SAFETY_LABELS.has(e.label);
    const corroborates = alarm !== undefined && (e.label === 'siren' || e.label === 'alarm');
    const urgency = corroborates ? 1 : this.priority(e);
    const dir =
      e.directionKnown && e.bearingDeg !== undefined ? describeDirection(e.bearingDeg).replace('to your ', '') : 'direction unknown';
    const label = LABEL_TEXT[e.label];
    const source = this.providerLabel(e.provider);
    const pct = Math.round(e.confidence * 100);

    const core = corroborates ? `${label}, ${dir}.` : `${label}, ${dir}.`;
    const suffix = `Inferred, ${source}.`;
    const short = trim(core, suffix);
    const long = corroborates
      ? `${label} ${dir === 'direction unknown' ? '' : `(${dir}) `}heard, ${pct}% confidence. This is consistent with the ${alarm.label} alarm, which is reported by a VERIFIED source and is authoritative. This sound label is only an inference.`
      : `${label} ${dir === 'direction unknown' ? 'from an unknown direction' : `from the ${dir}`}, ${pct}% confidence. This label is an inference from ${e.provider === 'scripted' ? 'a scripted (simulated) soundscape' : 'a local heuristic, not a trained model'}, so it can be wrong.${
          e.directionKnown
            ? ' Direction is left or right only; front and back cannot be told apart.'
            : ' Direction could not be estimated from this input.'
        }`;

    return guardPercept({
      id: this.deps.nextId(),
      timestamp: iso(this.deps.clock),
      sense: 'hearing',
      kind: corroborates ? 'status' : safety && urgency >= 3 ? 'alert' : 'description',
      urgency,
      short,
      long,
      ...(e.directionKnown && e.bearingDeg !== undefined ? { spatial: { bearingDeg: e.bearingDeg } } : {}),
      provenance: {
        tier: 'INFERRED',
        source: e.provider === 'scripted' ? 'sim-microphone' : 'device-microphone',
        sourceLabel: source,
        confidence: e.confidence,
        evidence: [
          `${e.provider} classifier`,
          `confidence ${pct}%`,
          e.directionKnown ? 'direction from left/right level difference' : 'no direction available',
        ],
      },
      ...(safety ? { safety: true } : {}),
      ...(e.provider === 'scripted' ? { simulated: true } : {}),
    });
  }

  private providerLabel(provider: string): string {
    return provider === 'scripted' ? 'simulated mic' : 'microphone';
  }

  module(events: () => AsyncIterable<SoundEvent>): SenseModule {
    return {
      id: 'hearing',
      inputs: ['device-microphone', 'verified alarm-feed'],
      produce: async function* (this: EchoSense) {
        for await (const e of events()) {
          const p = this.ingest(e);
          if (p) yield p;
        }
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
