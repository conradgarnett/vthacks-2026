import type { AudioClassifier, SoundEvent } from '@sense/providers';
import { dominantPeak, envelope, hann, magnitudeSpectrum, median, mono, nextPow2, percentile, rms, spectralCentroid } from './dsp';

/**
 * Left/right direction from the interaural LEVEL difference of a stereo input.
 * Honest limits: this only tells left from right, cannot tell front from back, and is only
 * meaningful when the two channels really are two spatially separated microphones. Mono input
 * gives no direction at all, and near-equal levels are reported as "direction unknown".
 */
export function estimateBearing(left: Float32Array, right: Float32Array): { bearingDeg: number; directionKnown: boolean } {
  const l = rms(left);
  const r = rms(right);
  if (l + r < 1e-6) return { bearingDeg: 0, directionKnown: false };
  const ild = (r - l) / (r + l); // -1..1
  if (Math.abs(ild) < 0.12) return { bearingDeg: 0, directionKnown: false };
  const pan = Math.max(-1, Math.min(1, (4 / Math.PI) * Math.atan(ild))); // invert constant-power panning
  const deg = (Math.asin(pan) * 180) / Math.PI;
  return { bearingDeg: Math.round(((deg % 360) + 360) % 360), directionKnown: true };
}

interface Frame {
  t: number;
  freq: number;
  peakiness: number;
  centroid: number;
}

function analyse(x: Float32Array, sampleRate: number): Frame[] {
  const n = nextPow2(Math.round(sampleRate * 0.064));
  const hop = n / 2;
  const w = hann(n);
  const out: Frame[] = [];
  for (let s = 0; s + n <= x.length; s += hop) {
    const frame = x.subarray(s, s + n);
    if (rms(frame) < 0.02) continue;
    const mag = magnitudeSpectrum(frame, w);
    const p = dominantPeak(mag, sampleRate, 200);
    out.push({ t: s / sampleRate, freq: p.freq, peakiness: p.peakiness, centroid: spectralCentroid(mag, sampleRate) });
  }
  return out;
}

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

function detectSiren(frames: Frame[]): number {
  const tonal = frames.filter((f) => f.peakiness > 8);
  if (tonal.length < 6) return 0;
  const inBand = tonal.filter((f) => f.freq >= 500 && f.freq <= 1800);
  if (inBand.length / tonal.length < 0.8) return 0;
  const freqs = inBand.map((f) => f.freq);
  const range = percentile(freqs, 95) - percentile(freqs, 5);
  if (range < 200) return 0; // a steady tone is not a siren
  // smooth sweeps only: a real siren changes gradually from frame to frame
  let jumps = 0;
  for (let i = 1; i < inBand.length; i++) if (Math.abs((inBand[i] as Frame).freq - (inBand[i - 1] as Frame).freq) > 250) jumps++;
  if (jumps / inBand.length > 0.15) return 0;
  return clamp01(0.4 + 0.3 * clamp01(range / 600) + 0.2 * clamp01(inBand.length / 20)) * 0.95;
}

function detectBeepAlarm(frames: Frame[], seconds: number): number {
  const high = frames.filter((f) => f.peakiness > 8 && f.freq >= 2000 && f.freq <= 4500);
  if (high.length < 4) return 0;
  // count on/off bursts by gaps in time between consecutive tonal frames
  let bursts = 1;
  for (let i = 1; i < high.length; i++) if ((high[i] as Frame).t - (high[i - 1] as Frame).t > 0.1) bursts++;
  if (bursts < 3) return 0;
  const freqs = high.map((f) => f.freq);
  if (percentile(freqs, 95) - percentile(freqs, 5) > 400) return 0; // beeps hold one pitch
  return clamp01(0.5 + 0.08 * bursts) * (seconds > 0.5 ? 0.9 : 0.6);
}

function detectKnock(x: Float32Array, sampleRate: number, centroid: number): { conf: number; count: number } {
  const env = envelope(x, sampleRate, 0.005);
  if (env.length < 20) return { conf: 0, count: 0 };
  const base = Math.max(median(env), 0.004);
  const minGap = Math.round(0.08 / 0.005);
  const onsets: number[] = [];
  for (let i = 1; i < env.length - 1; i++) {
    const v = env[i] as number;
    if (v < 0.05 || v < base * 6 || v < (env[i - 1] as number) || v < (env[i + 1] as number)) continue;
    const later = env[Math.min(env.length - 1, i + 12)] as number; // 60 ms later
    if (later > 0.45 * v) continue; // must decay fast: a knock, not a tone
    if (onsets.length === 0 || i - (onsets[onsets.length - 1] as number) >= minGap) onsets.push(i);
  }
  if (onsets.length < 2 || onsets.length > 10) return { conf: 0, count: onsets.length };
  const gaps = onsets.slice(1).map((o, k) => (o - (onsets[k] as number)) * 0.005);
  if (gaps.some((g) => g < 0.08 || g > 0.8)) return { conf: 0, count: onsets.length };
  if (centroid > 3000) return { conf: 0, count: onsets.length };
  return { conf: clamp01(0.45 + 0.1 * onsets.length), count: onsets.length };
}

/**
 * Local heuristic sound classifier: energy, spectral tonality, siren-like sweep detection,
 * beep-alarm detection and knock-like transient detection. It is a set of hand-written rules,
 * NOT a trained model, so its labels are guesses and it says "unknown" rather than stretching.
 */
export class LocalHeuristicClassifier implements AudioClassifier {
  readonly name = 'local-heuristic';

  classify(input: { left: Float32Array; right?: Float32Array; sampleRate: number; at: number }): SoundEvent[] {
    const { left, right, sampleRate, at } = input;
    const x = mono(left, right);
    const level = rms(x);
    if (level < 0.01) return [];
    const seconds = x.length / sampleRate;
    const frames = analyse(x, sampleRate);
    const centroid = frames.length ? median(frames.map((f) => f.centroid)) : 0;
    const dir = right ? estimateBearing(left, right) : { bearingDeg: 0, directionKnown: false };
    const make = (label: SoundEvent['label'], confidence: number): SoundEvent => ({
      label,
      confidence: Math.round(confidence * 100) / 100,
      ...(dir.directionKnown ? { bearingDeg: dir.bearingDeg } : {}),
      directionKnown: dir.directionKnown,
      provider: this.name,
      at,
    });

    const siren = detectSiren(frames);
    if (siren > 0.4) return [make('siren', siren)];
    const beep = detectBeepAlarm(frames, seconds);
    if (beep > 0.4) return [make('alarm', beep)];
    const knock = detectKnock(x, sampleRate, centroid);
    if (knock.conf > 0.4) return [make('knock', knock.conf)];
    // Loud but unrecognised: say so instead of guessing.
    return level > 0.05 ? [make('unknown', 0.2)] : [];
  }
}

/** Buffers audio chunks and classifies overlapping windows (for microphone streaming). */
export class WindowedClassifier {
  private left: number[] = [];
  private right: number[] = [];
  private consumed = 0;

  constructor(
    private readonly classifier: AudioClassifier,
    private readonly sampleRate: number,
    private readonly windowSec = 2,
    private readonly hopSec = 1,
  ) {}

  push(left: Float32Array, right?: Float32Array): SoundEvent[] {
    for (const v of left) this.left.push(v);
    if (right) for (const v of right) this.right.push(v);
    const size = Math.round(this.windowSec * this.sampleRate);
    const hop = Math.round(this.hopSec * this.sampleRate);
    const out: SoundEvent[] = [];
    while (this.left.length >= size) {
      const l = Float32Array.from(this.left.slice(0, size));
      const r = right ? Float32Array.from(this.right.slice(0, size)) : undefined;
      out.push(
        ...this.classifier.classify({
          left: l,
          ...(r ? { right: r } : {}),
          sampleRate: this.sampleRate,
          at: this.consumed / this.sampleRate,
        }),
      );
      this.left = this.left.slice(hop);
      if (right) this.right = this.right.slice(hop);
      this.consumed += hop;
    }
    return out;
  }
}
