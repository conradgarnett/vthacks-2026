/** Small, dependency-free DSP toolkit. Deterministic, pure, and safe to run in the browser or Node. */

export function rms(x: Float32Array): number {
  if (x.length === 0) return 0;
  let s = 0;
  for (let i = 0; i < x.length; i++) s += (x[i] as number) * (x[i] as number);
  return Math.sqrt(s / x.length);
}

export function nextPow2(n: number): number {
  let p = 1;
  while (p < n) p *= 2;
  return p;
}

export function hann(n: number): Float64Array {
  const w = new Float64Array(n);
  for (let i = 0; i < n; i++) w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1));
  return w;
}

/** In-place iterative radix-2 FFT. `re` and `im` must have the same power-of-two length. */
export function fft(re: Float64Array, im: Float64Array): void {
  const n = re.length;
  if (n & (n - 1)) throw new Error('fft length must be a power of two');
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j] as number, re[i] as number];
      [im[i], im[j]] = [im[j] as number, im[i] as number];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wr = Math.cos(ang);
    const wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1;
      let ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k;
        const b = i + k + len / 2;
        const tr = (re[b] as number) * cr - (im[b] as number) * ci;
        const ti = (re[b] as number) * ci + (im[b] as number) * cr;
        re[b] = (re[a] as number) - tr;
        im[b] = (im[a] as number) - ti;
        re[a] = (re[a] as number) + tr;
        im[a] = (im[a] as number) + ti;
        const ncr = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = ncr;
      }
    }
  }
}

/** Magnitude spectrum (first n/2 bins) of a Hann-windowed frame. */
export function magnitudeSpectrum(frame: Float32Array, window?: Float64Array): Float64Array {
  const n = frame.length;
  const w = window ?? hann(n);
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  for (let i = 0; i < n; i++) re[i] = (frame[i] as number) * (w[i] as number);
  fft(re, im);
  const mag = new Float64Array(n / 2);
  for (let i = 0; i < n / 2; i++) mag[i] = Math.hypot(re[i] as number, im[i] as number);
  return mag;
}

export interface SpectralPeak {
  freq: number;
  /** Peak magnitude divided by the mean magnitude: high means tonal, low means noise-like. */
  peakiness: number;
}

/** Dominant frequency above `minHz`, refined by parabolic interpolation. */
export function dominantPeak(mag: Float64Array, sampleRate: number, minHz = 100): SpectralPeak {
  const n = mag.length * 2;
  const binHz = sampleRate / n;
  let best = Math.max(1, Math.ceil(minHz / binHz));
  let sum = 0;
  for (let i = best; i < mag.length; i++) {
    sum += mag[i] as number;
    if ((mag[i] as number) > (mag[best] as number)) best = i;
  }
  const mean = sum / Math.max(1, mag.length - best);
  const a = mag[best - 1] ?? 0;
  const b = mag[best] as number;
  const c = mag[best + 1] ?? 0;
  const denom = a - 2 * b + c;
  const shift = denom === 0 ? 0 : (0.5 * (a - c)) / denom;
  return { freq: (best + shift) * binHz, peakiness: mean === 0 ? 0 : b / mean };
}

export function spectralCentroid(mag: Float64Array, sampleRate: number): number {
  const n = mag.length * 2;
  let num = 0;
  let den = 0;
  for (let i = 0; i < mag.length; i++) {
    num += ((i * sampleRate) / n) * (mag[i] as number);
    den += mag[i] as number;
  }
  return den === 0 ? 0 : num / den;
}

/** Peak-amplitude envelope in fixed steps (seconds per step). */
export function envelope(x: Float32Array, sampleRate: number, stepSec: number): Float64Array {
  const step = Math.max(1, Math.round(sampleRate * stepSec));
  const out = new Float64Array(Math.floor(x.length / step));
  for (let i = 0; i < out.length; i++) {
    let peak = 0;
    for (let j = i * step; j < (i + 1) * step; j++) peak = Math.max(peak, Math.abs(x[j] as number));
    out[i] = peak;
  }
  return out;
}

export function median(v: ArrayLike<number>): number {
  const a = Array.from(v).sort((x, y) => x - y);
  if (a.length === 0) return 0;
  const m = a.length >> 1;
  return a.length % 2 ? (a[m] as number) : ((a[m - 1] as number) + (a[m] as number)) / 2;
}

export function percentile(v: number[], p: number): number {
  if (v.length === 0) return 0;
  const a = [...v].sort((x, y) => x - y);
  return a[Math.min(a.length - 1, Math.max(0, Math.round((p / 100) * (a.length - 1))))] as number;
}

/** Mix to mono. */
export function mono(left: Float32Array, right?: Float32Array): Float32Array {
  if (!right) return left;
  const n = Math.min(left.length, right.length);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = ((left[i] as number) + (right[i] as number)) / 2;
  return out;
}
