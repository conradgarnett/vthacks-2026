import { describe, expect, it } from 'vitest';
import {
  MIN_ALERT_SPEECH_RATE,
  PERSONA_IDS,
  PERSONAS,
  SENSES,
  applySituation,
  buildProfile,
  estimateSpeechSeconds,
  getPersona,
  type Modality,
  type Percept,
  type PersonaId,
  type Sense,
} from '@sense/protocol';
import { route, selectModalities, speechText } from '../src';

const make = (over: Partial<Percept> = {}): Percept => ({
  id: 'p-1',
  timestamp: '2026-01-15T10:00:00.000Z',
  sense: 'hearing',
  kind: 'alert',
  urgency: 2,
  short: 'Fire alarm, East stairwell. Verified, Riverside Hall.',
  provenance: {
    tier: 'VERIFIED',
    source: 'riverside-hall.sim',
    sourceLabel: 'Riverside Hall',
    evidence: [],
  },
  spatial: { bearingDeg: 90, distanceM: 14, clockPosition: 3 },
  ...over,
});

const mods = (persona: PersonaId, over: Partial<Percept>): Modality[] => [...selectModalities(PERSONAS[persona], make(over))].sort();
const sorted = (...m: Modality[]) => [...m].sort();

describe('routing matrix: explicit expectations per persona', () => {
  it('Blind / low vision: audio first, escalating with urgency', () => {
    const v = (urgency: number, safety = false) => mods('blind', { sense: 'vision', urgency, safety });
    expect(v(0)).toEqual(sorted('speech'));
    expect(v(1)).toEqual(sorted('speech'));
    expect(v(2)).toEqual(sorted('speech', 'spatial-audio'));
    expect(v(3, true)).toEqual(sorted('speech', 'spatial-audio', 'haptic'));
    expect(v(4, true)).toEqual(sorted('speech', 'spatial-audio', 'haptic'));
  });

  it('Deaf / hard of hearing: visual and haptic, never speech or spatial audio', () => {
    const h = (urgency: number, safety = false) => mods('deaf', { sense: 'hearing', urgency, safety });
    expect(h(0)).toEqual(['visual']);
    expect(h(2)).toEqual(['visual']);
    expect(h(3, true)).toEqual(sorted('visual', 'haptic'));
    expect(h(4, true)).toEqual(sorted('visual', 'haptic'));
  });

  it('Motor-limited: visual plus speech, haptic when urgent', () => {
    const t = (urgency: number, safety = false) => mods('motor', { sense: 'touch', urgency, safety });
    expect(t(1)).toEqual(['visual']);
    expect(t(2)).toEqual(sorted('visual', 'speech'));
    expect(t(4, true)).toEqual(sorted('visual', 'speech', 'haptic'));
  });

  it('Cannot smell (ScentGuard): haptic joins visual early, everything at life-safety', () => {
    const s = (urgency: number, safety = true) => mods('anosmia', { sense: 'smell', urgency, safety });
    expect(s(1)).toEqual(['visual']);
    expect(s(2)).toEqual(sorted('visual', 'haptic'));
    expect(s(3)).toEqual(sorted('visual', 'haptic', 'speech'));
    expect(s(4)).toEqual(sorted('visual', 'haptic', 'speech', 'spatial-audio'));
  });

  it('Impaired taste (TasteLens): visual plus speech', () => {
    const t = (urgency: number, safety = false) => mods('ageusia', { sense: 'taste', urgency, safety });
    expect(t(0)).toEqual(['visual']);
    expect(t(2)).toEqual(sorted('visual', 'speech'));
    expect(t(4, true)).toEqual(sorted('visual', 'speech', 'haptic'));
  });
});

describe('routing rules', () => {
  it('a non-safety percept from an untranslated sense keeps only its primary modality', () => {
    // Deaf user, a vision-sense status at urgency 3 that is not safety-related: quiet visual only.
    expect(mods('deaf', { sense: 'vision', urgency: 3, safety: false })).toEqual(['visual']);
    // ...but the same percept flagged as safety-critical gets full escalation.
    expect(mods('deaf', { sense: 'vision', urgency: 3, safety: true })).toEqual(sorted('visual', 'haptic'));
  });

  it('safety percepts at urgency >= 3 always get at least two modalities, even for thin profiles', () => {
    const thin = buildProfile({
      name: 'Thin',
      output: {
        '0': ['visual'],
        '1': ['visual'],
        '2': ['visual'],
        '3': ['visual'],
        '4': ['speech'],
      },
    });
    expect(selectModalities(thin, make({ urgency: 3, safety: true })).length).toBeGreaterThanOrEqual(2);
    expect(selectModalities(thin, make({ urgency: 4, safety: true })).length).toBeGreaterThanOrEqual(2);
    expect(selectModalities(thin, make({ urgency: 4, safety: true }))).toContain('speech');
  });

  it('falls back to visual when a profile yields nothing', () => {
    const empty = buildProfile({
      name: 'Empty',
      output: { '0': [], '1': [], '2': [], '3': [], '4': [] },
    });
    expect(selectModalities(empty, make({ urgency: 1 }))).toEqual(['visual']);
  });

  it('per-sense overrides win over the default row', () => {
    const p = buildProfile({ name: 'Override', senseOverrides: { hearing: { '2': ['haptic', 'visual'] } } }, 'deaf');
    expect(selectModalities(p, make({ sense: 'hearing', urgency: 2 }))).toEqual(['haptic', 'visual']);
    expect(selectModalities(p, make({ sense: 'vision', urgency: 2 }))).toEqual(['visual']);
  });

  it('situational presets: noisy-room removes audio, hands-full adds speech', () => {
    const noisy = applySituation(PERSONAS.blind, 'noisy-room');
    const plan = route(make({ sense: 'vision', urgency: 4, safety: true }), noisy);
    expect(plan.speech).toBeUndefined();
    expect(plan.spatialAudio).toBeUndefined();
    expect(plan.modalities).toEqual(expect.arrayContaining(['visual', 'haptic']));
    const hands = route(make({ sense: 'hearing', urgency: 2 }), applySituation(PERSONAS.deaf, 'hands-full'));
    expect(hands.speech).toBeDefined();
  });

  it('interrupts from the profile’s interruption threshold', () => {
    const p = { ...getPersona('blind'), interruptFromUrgency: 2 };
    expect(route(make({ urgency: 1 }), p).interrupt).toBe(false);
    expect(route(make({ urgency: 2 }), p).interrupt).toBe(true);
    expect(route(make({ urgency: 4 }), p).speech?.interrupt).toBe(true);
  });
});

describe('routing invariants over every persona, sense, urgency and safety flag', () => {
  const cases = PERSONA_IDS.flatMap((persona) =>
    (SENSES as readonly Sense[]).flatMap((sense) =>
      [0, 1, 2, 3, 4].flatMap((urgency) =>
        [false, true].flatMap((safety) => [true, false].map((withSpatial) => ({ persona, sense, urgency, safety, withSpatial }))),
      ),
    ),
  );

  it(`holds for all ${cases.length} combinations`, () => {
    for (const c of cases) {
      const profile = PERSONAS[c.persona];
      const p = make({
        sense: c.sense,
        urgency: c.urgency,
        safety: c.safety,
        ...(c.withSpatial ? {} : { spatial: undefined }),
      });
      const plan = route(p, profile);
      const where = JSON.stringify(c);
      expect(plan.modalities.length, where).toBeGreaterThan(0);
      expect(new Set(plan.modalities).size, where).toBe(plan.modalities.length);
      expect(plan.aria, where).toBe(c.urgency >= 3 ? 'assertive' : 'polite');
      if (c.safety && c.urgency >= 3) expect(plan.modalities.length, where).toBeGreaterThanOrEqual(2);
      // captions for every audio output
      if (plan.speech || plan.spatialAudio) expect(plan.caption, where).toBeTruthy();
      else expect(plan.caption, where).toBeUndefined();
      // haptic or visual equivalent for every alert
      if (c.safety && c.urgency >= 3)
        expect(
          plan.modalities.some((m) => m === 'visual' || m === 'haptic'),
          where,
        ).toBe(true);
      // Deaf persona never receives audio-only channels
      if (c.persona === 'deaf') {
        expect(plan.speech, where).toBeUndefined();
        expect(plan.spatialAudio, where).toBeUndefined();
      }
      // Blind persona is never left with a visual-only alert
      if (c.persona === 'blind' && c.urgency >= 2) {
        expect(
          plan.modalities.some((m) => m === 'speech' || m === 'spatial-audio'),
          where,
        ).toBe(true);
      }
      if (plan.haptic && c.urgency > 0) expect(plan.haptic.pattern.length, where).toBeGreaterThan(0);
      if (plan.speech && c.safety && c.urgency >= 3 && p.kind === 'alert') {
        expect(plan.speech.rate, where).toBeGreaterThanOrEqual(MIN_ALERT_SPEECH_RATE);
        expect(estimateSpeechSeconds(p.short, plan.speech.rate), where).toBeLessThanOrEqual(2.05);
      }
    }
  });
});

describe('speech text', () => {
  const blind = getPersona('blind');
  it('hedges inferred and unverified non-safety content out loud', () => {
    const p = make({
      sense: 'vision',
      kind: 'description',
      short: 'Door ahead on your left.',
      spatial: undefined,
      provenance: { tier: 'INFERRED', source: 'device-camera', confidence: 0.72, evidence: [] },
    });
    expect(speechText(p, blind)).toBe('Door ahead on your left. Inferred 72 percent.');
    expect(speechText({ ...p, provenance: { ...p.provenance, tier: 'UNVERIFIED' } }, blind)).toContain('Unverified');
  });

  it('does not add a hedge to safety percepts (they already state tier and source)', () => {
    const t = speechText(make({ safety: true }), blind);
    expect(t).not.toMatch(/Inferred|Unverified\./);
    expect(t).toContain('Verified, Riverside Hall');
  });

  it('adds direction unless terse, and detail for detailed verbosity', () => {
    const p = make({ sense: 'vision', long: 'First sentence here. Second sentence.' });
    expect(speechText(p, { ...blind, verbosity: 'normal' })).toContain('to your right, 14 metres.');
    expect(speechText(p, { ...blind, verbosity: 'terse' })).not.toContain('metres');
    expect(speechText(p, { ...blind, verbosity: 'detailed' })).toContain('First sentence here.');
    expect(speechText(p, { ...blind, verbosity: 'detailed' })).not.toContain('Second sentence');
  });

  it('plans spatial audio from the bearing', () => {
    const right = route(make({ sense: 'vision', urgency: 2, spatial: { bearingDeg: 90 } }), blind);
    expect(right.spatialAudio?.pan).toBe(1);
    const left = route(make({ sense: 'vision', urgency: 2, spatial: { bearingDeg: 270 } }), blind);
    expect(left.spatialAudio?.pan).toBe(-1);
    const behind = route(make({ sense: 'vision', urgency: 2, spatial: { bearingDeg: 180 } }), blind);
    expect(behind.spatialAudio?.behind).toBe(true);
    const none = route(make({ sense: 'vision', urgency: 2, spatial: undefined }), blind);
    expect(none.spatialAudio?.pan).toBe(0);
    // With no speech planned, the caption for the earcon says so when the direction is unknown.
    const audioOnly = buildProfile({
      name: 'Earcons only',
      output: {
        '0': ['visual'],
        '1': ['visual'],
        '2': ['spatial-audio'],
        '3': ['spatial-audio', 'visual'],
        '4': ['spatial-audio', 'visual'],
      },
    });
    expect(route(make({ sense: 'vision', urgency: 2, spatial: undefined }), audioOnly).caption).toMatch(/direction unknown/);
  });
});
