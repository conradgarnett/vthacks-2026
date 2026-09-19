import { describe, expect, it, vi } from 'vitest';
import { getPersona, type Percept } from '@sense/protocol';
import {
  HapticRenderer,
  Presenter,
  SpatialAudioRenderer,
  SpeechRenderer,
  browserAudioEnv,
  browserHapticEnv,
  browserSpeechEnv,
  describeHaptic,
  earconDuration,
  earconSchedule,
  hapticDirection,
  hapticPattern,
  panFromBearing,
  pannerPosition,
  type UtteranceLike,
} from '../src';

describe('earcons', () => {
  it('are distinct per urgency and get faster and longer as urgency rises', () => {
    const sigs = [0, 1, 2, 3, 4].map((u) => JSON.stringify(earconSchedule(u)));
    expect(new Set(sigs).size).toBe(5);
    expect(earconSchedule(0)).toHaveLength(1);
    expect(earconSchedule(3).length).toBeGreaterThanOrEqual(3);
    expect(earconSchedule(4).length).toBeGreaterThan(earconSchedule(3).length);
    expect(earconDuration(4)).toBeGreaterThan(earconDuration(0));
    for (const u of [0, 1, 2, 3, 4]) {
      const starts = earconSchedule(u).map((n) => n.start);
      expect(starts).toEqual([...starts].sort((a, b) => a - b));
    }
    // clamps out-of-range input instead of throwing
    expect(earconSchedule(9)).toEqual(earconSchedule(4));
  });

  it('pans and positions by bearing', () => {
    expect(panFromBearing(0)).toBe(0);
    expect(panFromBearing(90)).toBe(1);
    expect(panFromBearing(270)).toBe(-1);
    expect(Math.abs(panFromBearing(180))).toBeLessThan(0.01);
    expect(pannerPosition(90, 2)).toEqual({ x: 2, y: 0, z: 0 });
    expect(pannerPosition(0, 2).z).toBe(-2); // ahead is -z
    expect(pannerPosition(180, 2).z).toBe(2);
    expect(pannerPosition(0, 100).z).toBe(-5); // distance is clamped
  });
});

describe('haptics', () => {
  it('encodes direction as a quick-tap prefix and urgency as the body', () => {
    expect(hapticDirection(10)).toBe('ahead');
    expect(hapticDirection(90)).toBe('right');
    expect(hapticDirection(180)).toBe('behind');
    expect(hapticDirection(270)).toBe('left');
    expect(hapticDirection(undefined)).toBe('none');
    expect(hapticPattern(0, 90)).toEqual([]);
    expect(hapticPattern(3)).toEqual([250, 100, 250, 100, 250]);
    const right = hapticPattern(4, 90);
    expect(right.slice(0, 5)).toEqual([40, 70, 40, 70, 160]);
    expect(right.slice(5)).toEqual([500, 150, 500, 150, 500, 150, 500]);
    expect(hapticPattern(4, 270).length).toBeGreaterThan(right.length);
  });

  it('every pattern has a text equivalent', () => {
    expect(describeHaptic(4, 90)).toBe('four very long buzzes, preceded by 2 quick taps (right)');
    expect(describeHaptic(1)).toBe('one short buzz');
  });

  it('falls back to "unsupported" so the UI can show the visual equivalent', () => {
    expect(new HapticRenderer({}).fire({ pattern: [100], description: 'x' })).toBe('unsupported');
    const vibrate = vi.fn(() => true);
    const r = new HapticRenderer({ vibrate });
    expect(r.fire({ pattern: [100, 50, 100], description: 'x' })).toBe('vibrated');
    expect(vibrate).toHaveBeenCalledWith([100, 50, 100]);
    expect(r.fire({ pattern: [], description: '' })).toBe('unsupported');
    expect(
      new HapticRenderer({ vibrate: () => false }).fire({ pattern: [10], description: 'x' }),
    ).toBe('unsupported');
  });
});

describe('speech renderer', () => {
  const makeEnv = () => {
    const spoken: UtteranceLike[] = [];
    const cancel = vi.fn();
    return {
      spoken,
      cancel,
      env: {
        synth: { speak: (u: UtteranceLike) => spoken.push(u), cancel },
        createUtterance: (text: string): UtteranceLike => ({ text, rate: 1, lang: '' }),
      },
    };
  };

  it('speaks with the planned rate and language, cancelling first when interrupting', () => {
    const { spoken, cancel, env } = makeEnv();
    const r = new SpeechRenderer(env);
    expect(r.available).toBe(true);
    expect(r.speak({ text: 'Fire alarm.', rate: 1.8, interrupt: true }, 'en-GB')).toBe('spoken');
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(spoken[0]).toMatchObject({ text: 'Fire alarm.', rate: 1.8, lang: 'en-GB' });
    r.speak({ text: 'Ambient.', rate: 1, interrupt: false });
    expect(cancel).toHaveBeenCalledTimes(1);
    r.cancel();
    expect(cancel).toHaveBeenCalledTimes(2);
  });

  it('degrades to captions-only when the Web Speech API is missing', () => {
    const r = new SpeechRenderer({});
    expect(r.available).toBe(false);
    expect(r.speak({ text: 'x', rate: 1, interrupt: false })).toBe('captions-only');
  });
});

describe('spatial audio renderer', () => {
  function fakeContext() {
    const oscillators: {
      type: string;
      frequency: { value: number };
      start: ReturnType<typeof vi.fn>;
    }[] = [];
    const panner = {
      panningModel: '',
      positionX: { value: 0 },
      positionY: { value: 0 },
      positionZ: { value: 0 },
      connect: vi.fn(),
    };
    const param = () => ({ value: 0, setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn() });
    const ctx = {
      currentTime: 10,
      destination: {},
      resume: vi.fn(),
      close: vi.fn(),
      createPanner: () => panner,
      createGain: () => ({ gain: param(), connect: vi.fn() }),
      createOscillator: () => {
        const o = {
          type: '',
          frequency: { value: 0 },
          connect: vi.fn(),
          start: vi.fn(),
          stop: vi.fn(),
        };
        oscillators.push(o);
        return o;
      },
    };
    return { ctx: ctx as unknown as AudioContext, oscillators, panner, raw: ctx };
  }

  it('plays the urgency earcon through an HRTF panner at the planned position', () => {
    const f = fakeContext();
    const r = new SpatialAudioRenderer({ createContext: () => f.ctx });
    const res = r.play({ urgency: 4, pan: 1, position: { x: 2, y: 0, z: -1 }, behind: false });
    expect(res).toBe('played');
    expect(f.panner.panningModel).toBe('HRTF');
    expect(f.panner.positionX.value).toBe(2);
    expect(f.panner.positionZ.value).toBe(-1);
    expect(f.oscillators).toHaveLength(earconSchedule(4).length);
    expect(f.oscillators[0]?.start).toHaveBeenCalledWith(10);
    r.close();
    expect(f.raw.close).toHaveBeenCalled();
  });

  it('reports unavailable without Web Audio', () => {
    expect(
      new SpatialAudioRenderer({ createContext: () => undefined }).play({
        urgency: 2,
        pan: 0,
        position: { x: 0, y: 0, z: 0 },
        behind: false,
      }),
    ).toBe('unavailable');
    expect(
      new SpatialAudioRenderer({}).play({
        urgency: 2,
        pan: 0,
        position: { x: 0, y: 0, z: 0 },
        behind: false,
      }),
    ).toBe('unavailable');
  });
});

describe('presenter', () => {
  const percept: Percept = {
    id: 'p-1',
    timestamp: '2026-01-15T10:00:00.000Z',
    sense: 'hearing',
    kind: 'alert',
    urgency: 4,
    short: 'Fire alarm, East stairwell. Verified, Riverside Hall.',
    provenance: {
      tier: 'VERIFIED',
      source: 'riverside-hall.sim',
      sourceLabel: 'Riverside Hall',
      evidence: [],
    },
    spatial: { bearingDeg: 90, distanceM: 14 },
    safety: true,
  };

  it('drives visual + haptic for the Deaf persona and reports the haptic fallback honestly', () => {
    const vibrate = vi.fn(() => true);
    const p = new Presenter(
      new SpeechRenderer({}),
      new SpatialAudioRenderer({}),
      new HapticRenderer({ vibrate }),
    );
    const res = p.present(percept, getPersona('deaf'));
    expect(res.plan.modalities.sort()).toEqual(['haptic', 'visual']);
    expect(res.speech).toBeUndefined();
    expect(res.audio).toBeUndefined();
    expect(res.haptic).toBe('vibrated');
    expect(vibrate).toHaveBeenCalled();
    const noVibration = new Presenter(
      new SpeechRenderer({}),
      new SpatialAudioRenderer({}),
      new HapticRenderer({}),
    );
    expect(noVibration.present(percept, getPersona('deaf')).haptic).toBe('unsupported');
  });

  it('drives speech + spatial audio for the Blind persona, degrading to captions when unavailable', () => {
    const p = new Presenter(
      new SpeechRenderer({}),
      new SpatialAudioRenderer({}),
      new HapticRenderer({}),
    );
    const res = p.present({ ...percept, sense: 'vision' }, getPersona('blind'));
    expect(res.speech).toBe('captions-only');
    expect(res.audio).toBe('unavailable');
    expect(res.plan.caption).toContain('Fire alarm');
  });

  it('browser environment factories are safe in Node (no APIs present)', () => {
    expect(browserSpeechEnv()).toEqual({});
    expect(browserAudioEnv().createContext?.()).toBeUndefined();
    expect(browserHapticEnv()).toEqual({});
  });
});
