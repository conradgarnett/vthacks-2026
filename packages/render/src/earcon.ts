import type { Urgency } from '@sense/protocol';

/** One note of an earcon (a short, non-speech audio cue). Times are seconds from the start. */
export interface EarconNote {
  freq: number;
  start: number;
  duration: number;
  wave: 'sine' | 'triangle' | 'square';
  gain: number;
}

/**
 * Earcons by urgency. They differ in pitch, rhythm and waveform so they can be told apart without
 * looking, and never rely on pitch alone: urgency 3 and 4 are also faster and repeated.
 */
export function earconSchedule(urgency: Urgency): EarconNote[] {
  switch (Math.min(4, Math.max(0, Math.round(urgency)))) {
    case 0:
      return [{ freq: 440, start: 0, duration: 0.08, wave: 'sine', gain: 0.15 }];
    case 1:
      return [
        { freq: 523, start: 0, duration: 0.1, wave: 'sine', gain: 0.2 },
        { freq: 659, start: 0.14, duration: 0.1, wave: 'sine', gain: 0.2 },
      ];
    case 2:
      return [
        { freq: 523, start: 0, duration: 0.12, wave: 'triangle', gain: 0.28 },
        { freq: 659, start: 0.16, duration: 0.12, wave: 'triangle', gain: 0.28 },
        { freq: 784, start: 0.32, duration: 0.16, wave: 'triangle', gain: 0.28 },
      ];
    case 3:
      return [0, 0.16, 0.32].map((start) => ({
        freq: 880,
        start,
        duration: 0.1,
        wave: 'square' as const,
        gain: 0.3,
      }));
    default: {
      const out: EarconNote[] = [];
      for (let i = 0; i < 6; i++) {
        out.push({
          freq: i % 2 === 0 ? 660 : 990,
          start: i * 0.18,
          duration: 0.16,
          wave: 'square',
          gain: 0.35,
        });
      }
      return out;
    }
  }
}

export function earconDuration(urgency: Urgency): number {
  return Math.max(...earconSchedule(urgency).map((n) => n.start + n.duration));
}

/** Stereo pan from a bearing relative to the user's heading: 90 deg = hard right, 270 = hard left. */
export function panFromBearing(bearingDeg: number): number {
  const rad = (((bearingDeg % 360) + 360) % 360) * (Math.PI / 180);
  return Math.round(Math.sin(rad) * 1000) / 1000 + 0; // + 0 normalises -0
}

/** 3D position for a PannerNode (metres, listener at origin facing -z, +x to the right). */
export function pannerPosition(bearingDeg: number, distanceM = 2): { x: number; y: number; z: number } {
  const rad = (((bearingDeg % 360) + 360) % 360) * (Math.PI / 180);
  const d = Math.min(Math.max(distanceM, 0.5), 5);
  const r = (v: number) => Math.round(v * 1000) / 1000 + 0; // + 0 normalises -0
  return { x: r(Math.sin(rad) * d), y: 0, z: r(-Math.cos(rad) * d) };
}

export type HapticDirection = 'ahead' | 'right' | 'behind' | 'left' | 'none';

/** Legend shown in the UI: 1 short pulse ahead, 2 right, 3 behind, 4 left. */
export function hapticDirection(bearingDeg: number | undefined): HapticDirection {
  if (bearingDeg === undefined) return 'none';
  const b = ((bearingDeg % 360) + 360) % 360;
  if (b < 45 || b >= 315) return 'ahead';
  if (b < 135) return 'right';
  if (b < 225) return 'behind';
  return 'left';
}

const PULSES: Record<HapticDirection, number> = { none: 0, ahead: 1, right: 2, behind: 3, left: 4 };

/** Vibration pattern (ms, alternating vibrate/pause): a direction prefix, then an urgency body. */
export function hapticPattern(urgency: Urgency, bearingDeg?: number): number[] {
  if (urgency === 0) return [];
  const bodies: number[][] = [[], [60], [120, 80, 120], [250, 100, 250, 100, 250], [500, 150, 500, 150, 500, 150, 500]];
  const body = bodies[Math.min(4, Math.max(0, Math.round(urgency)))] ?? [];
  const prefix: number[] = [];
  for (let i = 0; i < PULSES[hapticDirection(bearingDeg)]; i++) prefix.push(40, 70);
  return [...prefix, ...(prefix.length > 0 ? [160] : []), ...body];
}

/** Text equivalent of a vibration pattern, for devices without vibration and for the UI legend. */
export function describeHaptic(urgency: Urgency, bearingDeg?: number): string {
  const level =
    ['none', 'one short buzz', 'two medium buzzes', 'three long buzzes', 'four very long buzzes'][
      Math.min(4, Math.max(0, Math.round(urgency)))
    ] ?? 'none';
  const dir = hapticDirection(bearingDeg);
  const pulses = PULSES[dir];
  return `${level}${pulses > 0 ? `, preceded by ${pulses} quick tap${pulses > 1 ? 's' : ''} (${dir})` : ''}`;
}
