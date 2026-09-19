import { seededRandom } from '@sense/protocol';

/** Deterministic signal synthesis for tests and demos (sample audio generated on the device). */

export interface Stereo {
  left: Float32Array;
  right: Float32Array;
}

export function whiteNoise(samples: number, amp: number, seed = 1): Float32Array {
  const rnd = seededRandom(seed);
  const out = new Float32Array(samples);
  for (let i = 0; i < samples; i++) out[i] = (rnd() * 2 - 1) * amp;
  return out;
}

/** A "wail" siren: frequency sweeps smoothly between f0 and f1 with the given period. */
export function synthSiren(o: {
  sampleRate: number;
  seconds: number;
  f0?: number;
  f1?: number;
  periodSec?: number;
  amp?: number;
  noise?: number;
  seed?: number;
}): Float32Array {
  const { sampleRate, seconds, f0 = 600, f1 = 1400, periodSec = 2, amp = 0.5, noise = 0.01, seed = 1 } = o;
  const n = Math.round(sampleRate * seconds);
  const out = whiteNoise(n, noise, seed);
  let phase = 0;
  for (let i = 0; i < n; i++) {
    const t = i / sampleRate;
    const f = f0 + (f1 - f0) * (0.5 - 0.5 * Math.cos((2 * Math.PI * t) / periodSec));
    phase += (2 * Math.PI * f) / sampleRate;
    out[i] = (out[i] as number) + amp * Math.sin(phase);
  }
  return out;
}

/** Knock-like transients: short low-frequency bursts that decay quickly. */
export function synthKnock(o: {
  sampleRate: number;
  seconds: number;
  times: number[];
  amp?: number;
  noise?: number;
  seed?: number;
}): Float32Array {
  const { sampleRate, seconds, times, amp = 0.8, noise = 0.005, seed = 2 } = o;
  const n = Math.round(sampleRate * seconds);
  const out = whiteNoise(n, noise, seed);
  const rnd = seededRandom(seed + 100);
  for (const t0 of times) {
    const start = Math.round(t0 * sampleRate);
    for (let i = 0; i < sampleRate * 0.12 && start + i < n; i++) {
      const t = i / sampleRate;
      const env = Math.exp(-t / 0.018);
      out[start + i] = (out[start + i] as number) + amp * env * (0.8 * Math.sin(2 * Math.PI * 210 * t) + 0.2 * (rnd() * 2 - 1));
    }
  }
  return out;
}

/** Repeated pure-tone beeps (like a smoke-alarm pattern). */
export function synthBeeps(o: {
  sampleRate: number;
  seconds: number;
  freq?: number;
  onSec?: number;
  offSec?: number;
  amp?: number;
  noise?: number;
  seed?: number;
}): Float32Array {
  const { sampleRate, seconds, freq = 3100, onSec = 0.15, offSec = 0.15, amp = 0.5, noise = 0.005, seed = 3 } = o;
  const n = Math.round(sampleRate * seconds);
  const out = whiteNoise(n, noise, seed);
  for (let i = 0; i < n; i++) {
    const t = i / sampleRate;
    if (t % (onSec + offSec) < onSec) out[i] = (out[i] as number) + amp * Math.sin(2 * Math.PI * freq * t);
  }
  return out;
}

/** Place a mono source at a bearing (degrees clockwise from ahead) using constant-power level panning. */
export function spatialize(monoSignal: Float32Array, bearingDeg: number): Stereo {
  const pan = Math.sin((bearingDeg * Math.PI) / 180); // -1 hard left .. +1 hard right
  const a = ((pan + 1) * Math.PI) / 4;
  const gl = Math.cos(a);
  const gr = Math.sin(a);
  const left = new Float32Array(monoSignal.length);
  const right = new Float32Array(monoSignal.length);
  for (let i = 0; i < monoSignal.length; i++) {
    left[i] = (monoSignal[i] as number) * gl;
    right[i] = (monoSignal[i] as number) * gr;
  }
  return { left, right };
}

export function silence(samples: number, noise = 0.002, seed = 9): Float32Array {
  return whiteNoise(samples, noise, seed);
}
