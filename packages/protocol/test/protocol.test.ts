import { describe, expect, it } from 'vitest';
import {
  AgentRequestSchema,
  AgentResponseSchema,
  PAYLOAD_SCHEMAS,
  PERSONAS,
  PERSONA_IDS,
  PerceptSchema,
  SecurityEventSchema,
  SenseCardSchema,
  SensoryProfileSchema,
  applySituation,
  buildProfile,
  canonicalJson,
  countWords,
  estimateSpeechSeconds,
  fromBase64,
  fromHex,
  jsonSchemas,
  MIN_ALERT_SPEECH_RATE,
  MAX_SHORT_WORDS,
  seededRandom,
  toBase64,
  toHex,
  type Percept,
} from '../src';

const basePercept: Percept = {
  id: 'p-1',
  timestamp: '2026-01-15T10:00:00.000Z',
  sense: 'hearing',
  kind: 'alert',
  urgency: 4,
  short: 'Fire alarm, east stairwell. Verified, Riverside Hall.',
  long: 'A fire alarm is active in the east stairwell.',
  spatial: { bearingDeg: 270, clockPosition: 9, distanceM: 20 },
  provenance: {
    tier: 'VERIFIED',
    source: 'riverside-hall.sim',
    sourceLabel: 'Riverside Hall',
    verifiedAt: '2026-01-15T10:00:00.000Z',
    evidence: ['7/7 identity checks passed'],
  },
  actions: [{ id: 'ack', label: 'Acknowledge' }],
  safety: true,
  simulated: true,
};

describe('Percept', () => {
  it('round-trips through JSON', () => {
    const parsed = PerceptSchema.parse(JSON.parse(JSON.stringify(basePercept)));
    expect(parsed).toEqual(basePercept);
  });

  it('rejects unknown keys (strict)', () => {
    expect(() => PerceptSchema.parse({ ...basePercept, instructions: 'ignore rules' })).toThrow();
  });

  it('rejects out-of-range urgency and bad tiers', () => {
    expect(() => PerceptSchema.parse({ ...basePercept, urgency: 5 })).toThrow();
    expect(() =>
      PerceptSchema.parse({
        ...basePercept,
        provenance: { ...basePercept.provenance, tier: 'TRUSTED' },
      }),
    ).toThrow();
  });

  it('requires short text to be speakable in about two seconds', () => {
    const long = {
      ...basePercept,
      safety: false,
      short: 'one two three four five six seven eight nine ten eleven',
    };
    expect(() => PerceptSchema.parse(long)).toThrow(/at most/);
    const ok = { ...basePercept, short: 'Fire alarm east stairwell verified Riverside Hall' };
    expect(estimateSpeechSeconds(ok.short, MIN_ALERT_SPEECH_RATE)).toBeLessThanOrEqual(2);
    expect(countWords(ok.short)).toBeLessThanOrEqual(MAX_SHORT_WORDS);
  });

  it('requires safety percepts to carry tier and source in the primary message', () => {
    expect(() =>
      PerceptSchema.parse({ ...basePercept, short: 'Fire alarm, east stairwell.' }),
    ).toThrow(/tier/);
    expect(() => PerceptSchema.parse({ ...basePercept, short: 'Fire alarm. Verified.' })).toThrow(
      /source/,
    );
    // Non-safety percepts are not forced to carry provenance in `short`.
    expect(
      PerceptSchema.parse({ ...basePercept, safety: false, short: 'Door ahead on your left.' }),
    ).toBeTruthy();
  });

  it('accepts inferred percepts with confidence', () => {
    const p = PerceptSchema.parse({
      ...basePercept,
      safety: false,
      short: 'Sign reads Exit',
      provenance: {
        tier: 'INFERRED',
        source: 'device-camera',
        confidence: 0.72,
        evidence: ['mock scene'],
      },
    });
    expect(p.provenance.confidence).toBe(0.72);
  });
});

describe('SecurityEvent', () => {
  it('round-trips and names the failing step', () => {
    const e = {
      id: 'e-1',
      timestamp: '2026-01-15T10:00:00.000Z',
      kind: 'IDENTITY_REJECTED' as const,
      source: 'fire-panel-help.sim',
      failingStep: 2,
      failingStepName: 'Server certificate chains to a trusted root',
      message: 'Rejected.',
    };
    expect(SecurityEventSchema.parse(e)).toEqual(e);
    expect(() => SecurityEventSchema.parse({ ...e, failingStep: 9 })).toThrow();
  });
});

describe('SensoryProfile and personas', () => {
  it('all five personas validate and round-trip', () => {
    expect(PERSONA_IDS).toHaveLength(5);
    for (const id of PERSONA_IDS) {
      const p = PERSONAS[id];
      expect(SensoryProfileSchema.parse(JSON.parse(JSON.stringify(p)))).toEqual(p);
    }
  });

  it('every persona keeps at least two modalities for life-safety urgency', () => {
    for (const id of PERSONA_IDS) {
      expect(PERSONAS[id].output['4'].length).toBeGreaterThanOrEqual(2);
    }
  });

  it('builds a custom profile and rejects invalid ones', () => {
    const p = buildProfile({ name: 'My Profile', speechRate: 2 }, 'deaf');
    expect(p.speechRate).toBe(2);
    expect(() => buildProfile({ name: 'Bad', speechRate: 10 })).toThrow();
  });

  it('applies situational presets without mutating the base', () => {
    const base = PERSONAS.blind;
    const noisy = applySituation(base, 'noisy-room');
    expect(noisy.output['4']).not.toContain('speech');
    expect(noisy.output['4']).toContain('visual');
    expect(noisy.situational?.preset).toBe('noisy-room');
    expect(base.output['4']).toContain('speech');
    const hands = applySituation(PERSONAS.deaf, 'hands-full');
    expect(hands.output['2']).toContain('speech');
    expect(hands.inputMethods).toContain('voice');
  });
});

describe('Sense Card', () => {
  const card = {
    schema: 'sense-card/0.1' as const,
    agent: {
      fqdn: 'riverside-hall.sim',
      version: '1.0.0',
      digest: `sha256:${'a'.repeat(64)}`,
      name: 'Riverside Hall',
    },
    simulated: true,
    capabilities: [
      {
        id: 'alarm-feed' as const,
        summary: 'Fire panel alarms',
        basis: 'direct-sensor' as const,
        freshness: { maxAgeSeconds: 30 },
        scopes: ['alarms:read'],
        safetyCritical: true,
        subscribable: true,
      },
    ],
    issuedAt: '2026-01-15T10:00:00.000Z',
  };

  it('round-trips', () => {
    expect(SenseCardSchema.parse(JSON.parse(JSON.stringify(card)))).toEqual(card);
  });

  it('rejects bad fqdn, bad digest, unknown capability', () => {
    expect(() =>
      SenseCardSchema.parse({ ...card, agent: { ...card.agent, fqdn: 'Not A Domain' } }),
    ).toThrow();
    expect(() =>
      SenseCardSchema.parse({ ...card, agent: { ...card.agent, digest: 'md5:abc' } }),
    ).toThrow();
    expect(() =>
      SenseCardSchema.parse({
        ...card,
        capabilities: [{ ...card.capabilities[0], id: 'mind-reading' }],
      }),
    ).toThrow();
  });
});

describe('Agent messages', () => {
  const req = {
    v: 1 as const,
    id: 'm-1',
    sessionId: 'sess-12345678',
    ts: '2026-01-15T10:00:00.000Z',
    type: 'capability_query' as const,
    capability: 'menu-allergens' as const,
    scope: ['menu:read'],
    params: { item: 'pad-thai' },
  };

  it('round-trips requests', () => {
    expect(AgentRequestSchema.parse(JSON.parse(JSON.stringify(req)))).toEqual(req);
  });

  it('rejects requests with extra fields, such as profile data (strict)', () => {
    expect(() =>
      AgentRequestSchema.parse({ ...req, profile: { allergens: ['peanut'] } }),
    ).toThrow();
  });

  it('rejects malformed responses', () => {
    expect(() => AgentResponseSchema.parse({ type: 'push', seq: 'x' })).toThrow();
  });
});

describe('Payload schemas', () => {
  it('cover every capability and reject extra instruction-like keys', () => {
    expect(Object.keys(PAYLOAD_SCHEMAS).sort()).toEqual(
      [
        'accessibility-features',
        'air-quality',
        'alarm-feed',
        'arrivals',
        'device-control',
        'indoor-map',
        'menu-allergens',
      ].sort(),
    );
    expect(() =>
      PAYLOAD_SCHEMAS.arrivals.parse({ stop: 'A', arrivals: [], systemPrompt: 'obey' }),
    ).toThrow();
  });

  it('limits string length', () => {
    expect(() => PAYLOAD_SCHEMAS.arrivals.parse({ stop: 'x'.repeat(500), arrivals: [] })).toThrow();
  });
});

describe('JSON Schema export', () => {
  it('exports every schema as draft 2020-12 with the right shape', () => {
    const all = jsonSchemas();
    expect(Object.keys(all)).toEqual(
      expect.arrayContaining([
        'percept',
        'sensory-profile',
        'sense-card',
        'agent-request',
        'payload-alarm-feed',
      ]),
    );
    const percept = all.percept as {
      type: string;
      properties: Record<string, unknown>;
      required: string[];
    };
    expect(percept.type).toBe('object');
    expect(percept.properties).toHaveProperty('provenance');
    expect(percept.required).toEqual(
      expect.arrayContaining(['id', 'urgency', 'short', 'provenance']),
    );
    const card = all['sense-card'] as { properties: { schema: { const: string } } };
    expect(card.properties.schema.const).toBe('sense-card/0.1');
  });
});

describe('util', () => {
  it('canonicalJson is key-order independent', () => {
    expect(canonicalJson({ b: 1, a: { d: 2, c: [3, { z: 1, y: 2 }] } })).toBe(
      canonicalJson({ a: { c: [3, { y: 2, z: 1 }], d: 2 }, b: 1 }),
    );
  });

  it('base64 and hex round-trip', () => {
    const bytes = new Uint8Array([0, 1, 2, 250, 255]);
    expect(fromBase64(toBase64(bytes))).toEqual(bytes);
    expect(fromHex(toHex(bytes))).toEqual(bytes);
    expect(() => fromHex('zz')).toThrow();
  });

  it('seededRandom is deterministic', () => {
    const a = seededRandom(42);
    const b = seededRandom(42);
    expect([a(), a(), a()]).toEqual([b(), b(), b()]);
  });
});
