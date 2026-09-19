import { MIN_ALERT_SPEECH_RATE, describeDirection, type Modality, type Percept, type SensoryProfile, type Urgency } from '@sense/protocol';
import { describeHaptic, hapticPattern, panFromBearing, pannerPosition } from './earcon';

export type Emphasis = 'banner' | 'card' | 'quiet';

export interface RenderPlan {
  perceptId: string;
  urgency: Urgency;
  /** Modalities chosen from the profile (after safety redundancy rules). */
  modalities: Modality[];
  /** How the visual card is emphasised. A text card is ALWAYS present in the feed, for everyone. */
  visual: { emphasis: Emphasis; tier: Percept['provenance']['tier']; simulated: boolean };
  speech?: { text: string; rate: number; interrupt: boolean };
  spatialAudio?: {
    urgency: Urgency;
    pan: number;
    position: { x: number; y: number; z: number };
    behind: boolean;
  };
  haptic?: { pattern: number[]; description: string };
  /** Caption for every audio output. Present whenever speech or spatial audio is planned. */
  caption?: string;
  /** ARIA live politeness for the feed item. */
  aria: 'assertive' | 'polite';
  /** True when this should interrupt whatever is currently being presented. */
  interrupt: boolean;
}

const KEYS = ['0', '1', '2', '3', '4'] as const;

/** Modalities the profile asks for at this urgency, before the safety and translation rules. */
function baseModalities(profile: SensoryProfile, percept: Percept): Modality[] {
  const key = KEYS[percept.urgency] as (typeof KEYS)[number];
  return [...(profile.senseOverrides?.[percept.sense]?.[key] ?? profile.output[key] ?? ['visual'])];
}

/**
 * Route one percept to modalities for one profile. Rules, in order:
 *  1. Start from the profile's modalities for the percept's urgency (per-sense override first).
 *  2. A non-safety percept from a sense the user does not need translated keeps only its primary
 *     modality: no haptic or spatial-audio escalation for information they can perceive natively.
 *  3. Safety percepts at urgency >= 3 always get at least two modalities (redundancy), and never
 *     fewer than what the profile asked for.
 *  4. Something is always rendered: if the profile yields nothing, fall back to visual.
 */
export function selectModalities(profile: SensoryProfile, percept: Percept): Modality[] {
  let m = baseModalities(profile, percept);
  if (!profile.translate[percept.sense] && !percept.safety) m = m.slice(0, 1);
  if (percept.safety && percept.urgency >= 3 && m.length < 2) {
    for (const extra of ['visual', 'haptic', 'speech'] as const) {
      if (m.length >= 2) break;
      if (!m.includes(extra)) m.push(extra);
    }
  }
  if (m.length === 0) m = ['visual'];
  return Array.from(new Set(m));
}

/** Text spoken aloud. Non-safety inferred or unverified content is hedged out loud. */
export function speechText(percept: Percept, profile: SensoryProfile): string {
  const parts = [percept.short.replace(/\s+$/, '')];
  const p = percept.provenance;
  const hedge = !percept.safety && p.tier !== 'VERIFIED';
  if (percept.spatial && profile.verbosity !== 'terse') {
    const dir = describeDirection(percept.spatial.bearingDeg);
    const dist = percept.spatial.distanceM !== undefined ? `, ${Math.round(percept.spatial.distanceM)} metres` : '';
    if (!percept.short.toLowerCase().includes(dir)) parts.push(`${dir}${dist}.`);
  }
  if (hedge) {
    const conf = p.confidence !== undefined ? ` ${Math.round(p.confidence * 100)} percent` : '';
    parts.push(`${p.tier === 'INFERRED' ? 'Inferred' : 'Unverified'}${conf}.`);
  }
  if (profile.verbosity === 'detailed' && percept.long) {
    parts.push(percept.long.split(/(?<=[.!?])\s/)[0] ?? '');
  }
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

export function route(percept: Percept, profile: SensoryProfile): RenderPlan {
  const modalities = selectModalities(profile, percept);
  const critical = percept.urgency >= 3;
  const interrupt = percept.urgency >= profile.interruptFromUrgency;
  const plan: RenderPlan = {
    perceptId: percept.id,
    urgency: percept.urgency,
    modalities,
    visual: {
      emphasis: modalities.includes('visual') ? (critical ? 'banner' : 'card') : 'quiet',
      tier: percept.provenance.tier,
      simulated: percept.simulated ?? false,
    },
    aria: critical ? 'assertive' : 'polite',
    interrupt,
  };

  if (modalities.includes('speech')) {
    const text = speechText(percept, profile);
    plan.speech = {
      text,
      // Alerts are spoken fast enough that a 10-word message fits in about two seconds.
      rate: percept.kind === 'alert' && critical ? Math.max(profile.speechRate, MIN_ALERT_SPEECH_RATE) : profile.speechRate,
      interrupt,
    };
    plan.caption = text;
  }

  if (modalities.includes('spatial-audio')) {
    const bearing = percept.spatial?.bearingDeg;
    plan.spatialAudio = {
      urgency: percept.urgency,
      pan: bearing === undefined ? 0 : panFromBearing(bearing),
      position:
        bearing === undefined
          ? { x: 0, y: 0, z: -1 }
          : pannerPosition(bearing, percept.spatial?.distanceM ? Math.min(percept.spatial.distanceM / 5, 5) : 2),
      behind: bearing !== undefined && bearing > 90 && bearing < 270,
    };
    plan.caption ??= `${percept.short} (${percept.spatial ? describeDirection(percept.spatial.bearingDeg) : 'direction unknown'})`;
  }

  if (modalities.includes('haptic')) {
    plan.haptic = {
      pattern: hapticPattern(percept.urgency, percept.spatial?.bearingDeg),
      description: describeHaptic(percept.urgency, percept.spatial?.bearingDeg),
    };
  }

  return plan;
}
