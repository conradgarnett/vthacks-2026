import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { ManualClock, containsAssurance, getPersona, sequentialIds, type SensoryProfile } from '@sense/protocol';
import { SOUND_LABELS, type SoundEvent } from '@sense/providers';
import {
  DEMO_SOUNDSCAPE,
  EchoSense,
  LocalHeuristicClassifier,
  ScriptedSoundscape,
  WindowedClassifier,
  dominantPeak,
  envelope,
  estimateBearing,
  fft,
  hann,
  magnitudeSpectrum,
  median,
  mono,
  nextPow2,
  percentile,
  rms,
  silence,
  spatialize,
  spectralCentroid,
  synthBeeps,
  synthKnock,
  synthSiren,
  whiteNoise,
} from '../src';

const SR = 16_000;
const classifier = new LocalHeuristicClassifier();
const classify = (left: Float32Array, right?: Float32Array) =>
  classifier.classify({ left, ...(right ? { right } : {}), sampleRate: SR, at: 0 });

describe('DSP toolkit', () => {
  it('FFT puts a sine at the right bin, and dominantPeak refines the frequency', () => {
    const n = 2048;
    const f = 1000;
    const x = new Float32Array(n).map((_, i) => Math.sin((2 * Math.PI * f * i) / SR));
    const mag = magnitudeSpectrum(x, hann(n));
    const peak = dominantPeak(mag, SR);
    expect(Math.abs(peak.freq - f)).toBeLessThan(f * 0.01);
    expect(peak.peakiness).toBeGreaterThan(20);
    const noise = dominantPeak(magnitudeSpectrum(whiteNoise(n, 0.5, 4), hann(n)), SR);
    expect(noise.peakiness).toBeLessThan(peak.peakiness / 3);
  });

  it('FFT of an impulse is flat and non-power-of-two lengths are rejected', () => {
    const re = new Float64Array(8);
    const im = new Float64Array(8);
    re[0] = 1;
    fft(re, im);
    expect(Array.from(re)).toEqual(Array(8).fill(1));
    expect(() => fft(new Float64Array(6), new Float64Array(6))).toThrow(/power of two/);
    expect(nextPow2(1000)).toBe(1024);
  });

  it('rms, envelope, median, percentile, centroid and mono behave', () => {
    expect(rms(new Float32Array([1, -1, 1, -1]))).toBe(1);
    expect(rms(new Float32Array(0))).toBe(0);
    const env = envelope(new Float32Array([0, 0.5, -0.9, 0.1, 0, 0]), 1000, 0.002);
    expect(Array.from(env)).toEqual([0.5, 0.8999999761581421, 0]);
    expect(median([3, 1, 2])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
    expect(percentile([1, 2, 3, 4, 5], 100)).toBe(5);
    const lowTone = new Float32Array(1024).map((_, i) => Math.sin((2 * Math.PI * 300 * i) / SR));
    const highTone = new Float32Array(1024).map((_, i) => Math.sin((2 * Math.PI * 3000 * i) / SR));
    expect(spectralCentroid(magnitudeSpectrum(lowTone), SR)).toBeLessThan(spectralCentroid(magnitudeSpectrum(highTone), SR));
    expect(Array.from(mono(new Float32Array([1, 0]), new Float32Array([0, 1])))).toEqual([0.5, 0.5]);
  });
});

describe('local heuristic classifier (synthesized audio, seeded)', () => {
  it('recognises a siren-like wail, even with background noise', () => {
    const clean = classify(synthSiren({ sampleRate: SR, seconds: 2 }));
    expect(clean).toHaveLength(1);
    expect(clean[0]).toMatchObject({ label: 'siren', provider: 'local-heuristic', directionKnown: false });
    expect(clean[0]?.confidence).toBeGreaterThan(0.5);
    const noisy = synthSiren({ sampleRate: SR, seconds: 2, noise: 0.08, seed: 5 });
    expect(classify(noisy)[0]?.label).toBe('siren');
  });

  it('does not call a steady tone a siren', () => {
    const tone = synthSiren({ sampleRate: SR, seconds: 2, f0: 1000, f1: 1000 });
    expect(classify(tone)[0]?.label).not.toBe('siren');
  });

  it('recognises knock-like transients (two or more), not a single thump', () => {
    const knocks = classify(synthKnock({ sampleRate: SR, seconds: 2, times: [0.2, 0.6, 1.0] }));
    expect(knocks[0]).toMatchObject({ label: 'knock' });
    expect(knocks[0]?.confidence).toBeGreaterThan(0.6);
    const thump = classify(synthKnock({ sampleRate: SR, seconds: 2, times: [0.5] }));
    expect(thump[0]?.label ?? 'none').not.toBe('knock');
  });

  it('recognises a beeping alarm pattern', () => {
    const beeps = classify(synthBeeps({ sampleRate: SR, seconds: 2 }));
    expect(beeps[0]).toMatchObject({ label: 'alarm' });
    expect(beeps[0]?.confidence).toBeGreaterThan(0.5);
  });

  it('says "unknown" for loud noise (low confidence) and nothing for silence', () => {
    const loud = classify(whiteNoise(SR * 2, 0.6, 7));
    expect(loud[0]).toMatchObject({ label: 'unknown', confidence: 0.2 });
    expect(classify(silence(SR * 2))).toEqual([]);
  });

  it('is deterministic', () => {
    const a = classify(synthSiren({ sampleRate: SR, seconds: 2, seed: 3 }));
    const b = classify(synthSiren({ sampleRate: SR, seconds: 2, seed: 3 }));
    expect(a).toEqual(b);
  });
});

describe('direction from stereo level difference', () => {
  it.each([30, 60, 300, 330, 75])('recovers a source at %i degrees within 12 degrees', (bearing) => {
    const s = spatialize(synthSiren({ sampleRate: SR, seconds: 2 }), bearing);
    const e = classify(s.left, s.right)[0] as SoundEvent;
    expect(e.label).toBe('siren');
    expect(e.directionKnown).toBe(true);
    const err = Math.abs((((e.bearingDeg as number) - bearing + 540) % 360) - 180);
    expect(err).toBeLessThanOrEqual(12);
  });

  it('says direction is unknown for mono input and for near-equal levels', () => {
    expect(classify(synthSiren({ sampleRate: SR, seconds: 2 }))[0]?.directionKnown).toBe(false);
    const centre = spatialize(synthSiren({ sampleRate: SR, seconds: 2 }), 0);
    const e = classify(centre.left, centre.right)[0] as SoundEvent;
    expect(e.directionKnown).toBe(false);
    expect(e.bearingDeg).toBeUndefined();
    expect(estimateBearing(new Float32Array(10), new Float32Array(10)).directionKnown).toBe(false);
  });
});

describe('windowed streaming', () => {
  it('classifies a stream fed in small chunks, and reports stream time', () => {
    const w = new WindowedClassifier(classifier, SR, 2, 1);
    const stream = synthSiren({ sampleRate: SR, seconds: 5 });
    const events: SoundEvent[] = [];
    const chunk = SR / 4;
    for (let i = 0; i + chunk <= stream.length; i += chunk) events.push(...w.push(stream.subarray(i, i + chunk)));
    expect(events.length).toBe(4);
    expect(events.every((e) => e.label === 'siren')).toBe(true);
    expect(events.map((e) => e.at)).toEqual([0, 1, 2, 3]); // 2 s windows every 1 s over 5 s
  });
});

describe('scripted soundscape', () => {
  it('fires events once, in order, when the timeline reaches them, labelled as scripted', () => {
    const s = new ScriptedSoundscape(DEMO_SOUNDSCAPE);
    expect(s.due(-1)).toEqual([]);
    expect(s.due(0).map((e) => e.label)).toEqual(['knock']);
    expect(s.due(0)).toEqual([]);
    expect(s.due(4.5).map((e) => e.label)).toEqual(['dog-bark', 'siren']);
    const last = s.due(100);
    expect(last[0]).toMatchObject({ label: 'vehicle', directionKnown: false, provider: 'scripted' });
    s.reset();
    expect(s.due(0)).toHaveLength(1);
  });
});

describe('Echo percepts', () => {
  function make(persona: Parameters<typeof getPersona>[0] = 'deaf', alarm?: { label: string; fqdn: string }) {
    const clock = new ManualClock();
    const profile: SensoryProfile = getPersona(persona);
    const echo = new EchoSense({ clock, nextId: sequentialIds('p'), getProfile: () => profile, verifiedAlarm: () => alarm });
    return { echo, clock };
  }
  const siren = (over: Partial<SoundEvent> = {}): SoundEvent => ({
    label: 'siren',
    confidence: 0.78,
    bearingDeg: 270,
    directionKnown: true,
    provider: 'local-heuristic',
    at: 0,
    ...over,
  });

  it('an inferred siren is a safety alert that shows its tier, source and confidence, with direction', () => {
    const { echo } = make();
    const p = echo.ingest(siren()) as NonNullable<ReturnType<typeof echo.ingest>>;
    expect(p.short).toBe('Siren-like sound, left. Inferred, microphone.');
    expect(p.provenance).toMatchObject({ tier: 'INFERRED', source: 'device-microphone', confidence: 0.78 });
    expect(p.urgency).toBe(3);
    expect(p.kind).toBe('alert');
    expect(p.safety).toBe(true);
    expect(p.spatial?.bearingDeg).toBe(270);
    expect(p.long).toMatch(/78% confidence/);
    expect(p.long).toMatch(/inference from a local heuristic, not a trained model/);
    expect(p.long).toMatch(/front and back cannot be told apart/);
  });

  it('says direction is unknown when the input allowed no estimate', () => {
    const { echo } = make();
    const p = echo.ingest(siren({ directionKnown: false, bearingDeg: undefined as never })) as NonNullable<ReturnType<typeof echo.ingest>>;
    expect(p.short).toBe('Siren-like sound, direction unknown. Inferred, microphone.');
    expect(p.spatial).toBeUndefined();
    expect(p.long).toMatch(/Direction could not be estimated/);
  });

  it('personalises priority: a Deaf user gets knocks and doorbells one level higher', () => {
    const knock: SoundEvent = {
      label: 'knock',
      confidence: 0.72,
      bearingDeg: 350,
      directionKnown: true,
      provider: 'local-heuristic',
      at: 0,
    };
    expect(make('deaf').echo.priority(knock)).toBe(3);
    expect(make('blind').echo.priority(knock)).toBe(2);
    expect(make('deaf').echo.priority({ ...knock, label: 'doorbell' })).toBe(3);
  });

  it('low-confidence guesses stay ambient', () => {
    const { echo } = make();
    expect(echo.priority(siren({ confidence: 0.3 }))).toBe(1);
  });

  it('debounces repeats of the same label for a few seconds', () => {
    const { echo, clock } = make();
    expect(echo.ingest(siren())).not.toBeNull();
    clock.advance(2000);
    expect(echo.ingest(siren())).toBeNull();
    clock.advance(3000);
    expect(echo.ingest(siren())).not.toBeNull();
  });

  it('a VERIFIED building alarm dominates: an inferred siren is demoted to a corroborating note', () => {
    const { echo } = make('deaf', { label: 'Riverside Hall', fqdn: 'riverside-hall.sim' });
    const p = echo.ingest(siren()) as NonNullable<ReturnType<typeof echo.ingest>>;
    expect(p.urgency).toBe(1);
    expect(p.kind).toBe('status');
    expect(p.provenance.tier).toBe('INFERRED');
    expect(p.long).toMatch(/consistent with the Riverside Hall alarm, which is reported by a VERIFIED source and is authoritative/);
    // non-alarm sounds are unaffected
    const knock = echo.ingest({
      label: 'knock',
      confidence: 0.7,
      directionKnown: false,
      provider: 'local-heuristic',
      at: 1,
    }) as NonNullable<ReturnType<typeof echo.ingest>>;
    expect(knock.urgency).toBe(3);
  });

  it('scripted sounds are flagged simulated and use the simulated mic label', () => {
    const { echo } = make();
    const p = echo.ingest(siren({ provider: 'scripted' })) as NonNullable<ReturnType<typeof echo.ingest>>;
    expect(p.simulated).toBe(true);
    expect(p.short).toContain('simulated mic');
    expect(p.provenance.source).toBe('sim-microphone');
  });

  it('INVARIANT: an inferred sound never exceeds urgency 3, is always INFERRED, speakable, and never reassures (property)', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...SOUND_LABELS),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.boolean(),
        fc.integer({ min: 0, max: 359 }),
        fc.constantFrom('deaf', 'blind', 'motor', 'anosmia', 'ageusia') as fc.Arbitrary<Parameters<typeof getPersona>[0]>,
        (label, confidence, known, bearing, persona) => {
          const { echo } = make(persona);
          const p = echo.ingest({
            label,
            confidence,
            directionKnown: known,
            ...(known ? { bearingDeg: bearing } : {}),
            provider: 'local-heuristic',
            at: 0,
          });
          expect(p).not.toBeNull();
          expect(p?.urgency).toBeLessThanOrEqual(3);
          expect(p?.provenance.tier).toBe('INFERRED');
          expect(p?.provenance.confidence).toBeDefined();
          expect((p?.short ?? '').split(/\s+/).length).toBeLessThanOrEqual(10);
          expect(containsAssurance(p?.short ?? '')).toBe(false);
          expect(containsAssurance(p?.long ?? '')).toBe(false);
        },
      ),
      { numRuns: 200 },
    );
  });

  it('implements the SenseModule seam', async () => {
    const { echo } = make();
    const mod = echo.module(async function* () {
      yield siren();
      yield siren(); // repeat: dropped
    });
    const out = [];
    for await (const p of mod.produce()) out.push(p);
    expect(mod.id).toBe('hearing');
    expect(out).toHaveLength(1);
  });
});
