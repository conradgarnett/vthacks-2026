import type { Modality, ModalitiesByUrgency, PersonaId, SensoryProfile } from './profile';
import { SensoryProfileSchema } from './profile';
import { SENSES, type Sense } from './percept';

function byUrgency(
  m0: Modality[],
  m1: Modality[],
  m2: Modality[],
  m3: Modality[],
  m4: Modality[],
): ModalitiesByUrgency {
  return { '0': m0, '1': m1, '2': m2, '3': m3, '4': m4 };
}

function translating(...senses: Sense[]): Record<Sense, boolean> {
  return Object.fromEntries(SENSES.map((s) => [s, senses.includes(s)])) as Record<Sense, boolean>;
}

const baseDisplay = { highContrast: false, textScale: 1, reducedMotion: false };

export const PERSONAS: Record<PersonaId, SensoryProfile> = {
  blind: {
    id: 'persona-blind',
    name: 'Blind / low vision',
    personaId: 'blind',
    translate: translating('vision', 'hearing', 'smell', 'taste'),
    output: byUrgency(
      ['speech'],
      ['speech'],
      ['speech', 'spatial-audio'],
      ['speech', 'spatial-audio', 'haptic'],
      ['speech', 'spatial-audio', 'haptic'],
    ),
    verbosity: 'normal',
    speechRate: 1.2,
    interruptFromUrgency: 3,
    language: 'en',
    display: { highContrast: true, textScale: 1.5, reducedMotion: false },
    inputMethods: ['keyboard', 'touch', 'voice'],
    allergens: [],
  },
  deaf: {
    id: 'persona-deaf',
    name: 'Deaf / hard of hearing',
    personaId: 'deaf',
    translate: translating('hearing'),
    output: byUrgency(
      ['visual'],
      ['visual'],
      ['visual'],
      ['visual', 'haptic'],
      ['visual', 'haptic'],
    ),
    verbosity: 'normal',
    speechRate: 1,
    interruptFromUrgency: 3,
    language: 'en',
    display: baseDisplay,
    inputMethods: ['touch', 'keyboard'],
    allergens: [],
  },
  motor: {
    id: 'persona-motor',
    name: 'Motor-limited (Touchless)',
    personaId: 'motor',
    translate: translating('touch'),
    output: byUrgency(
      ['visual'],
      ['visual'],
      ['visual', 'speech'],
      ['visual', 'speech', 'haptic'],
      ['visual', 'speech', 'haptic'],
    ),
    verbosity: 'terse',
    speechRate: 1.1,
    interruptFromUrgency: 3,
    language: 'en',
    display: { highContrast: false, textScale: 1.25, reducedMotion: true },
    inputMethods: ['switch', 'gaze', 'head', 'keyboard'],
    allergens: [],
  },
  anosmia: {
    id: 'persona-anosmia',
    name: 'Cannot smell (ScentGuard)',
    personaId: 'anosmia',
    translate: translating('smell'),
    output: byUrgency(
      ['visual'],
      ['visual'],
      ['visual', 'haptic'],
      ['visual', 'haptic', 'speech'],
      ['visual', 'haptic', 'speech', 'spatial-audio'],
    ),
    verbosity: 'normal',
    speechRate: 1.1,
    interruptFromUrgency: 2,
    language: 'en',
    display: baseDisplay,
    inputMethods: ['touch', 'keyboard'],
    allergens: [],
  },
  ageusia: {
    id: 'persona-ageusia',
    name: 'Impaired taste (TasteLens)',
    personaId: 'ageusia',
    translate: translating('taste'),
    output: byUrgency(
      ['visual'],
      ['visual'],
      ['visual', 'speech'],
      ['visual', 'speech', 'haptic'],
      ['visual', 'speech', 'haptic'],
    ),
    verbosity: 'detailed',
    speechRate: 1,
    interruptFromUrgency: 3,
    language: 'en',
    display: baseDisplay,
    inputMethods: ['touch', 'keyboard'],
    allergens: [],
  },
};

export function getPersona(id: PersonaId): SensoryProfile {
  return structuredClone(PERSONAS[id]);
}

/**
 * Custom profile builder. Starts from a persona (or blank defaults) and applies overrides,
 * then validates. Throws a ZodError for invalid input.
 */
export function buildProfile(
  overrides: Partial<SensoryProfile> & { name: string },
  from: PersonaId = 'deaf',
): SensoryProfile {
  const base = getPersona(from);
  return SensoryProfileSchema.parse({
    ...base,
    id: `custom-${overrides.name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .slice(0, 40)}`,
    personaId: undefined,
    ...overrides,
  });
}

/**
 * Situational presets layer a temporary override over the base profile.
 * - hands-full: prefer speech + haptic, keep visual for confirmation, enable hands-free inputs.
 * - noisy-room: drop speech and spatial audio, use visual + haptic instead.
 * Life-safety urgency always keeps at least two modalities.
 */
export function applySituation(
  profile: SensoryProfile,
  preset: 'hands-full' | 'noisy-room',
  expiresAt?: string,
): SensoryProfile {
  const out = structuredClone(profile);
  const keys = ['0', '1', '2', '3', '4'] as const;
  for (const k of keys) {
    const current = out.output[k] ?? [];
    let next: Modality[];
    if (preset === 'noisy-room') {
      next = current.filter((m) => m !== 'speech' && m !== 'spatial-audio');
      if (!next.includes('visual')) next.push('visual');
      if ((k === '3' || k === '4') && !next.includes('haptic')) next.push('haptic');
    } else {
      next = [...current];
      if (!next.includes('speech')) next.push('speech');
      if (Number(k) >= 2 && !next.includes('haptic')) next.push('haptic');
    }
    out.output[k] = next;
  }
  if (preset === 'hands-full') {
    out.inputMethods = Array.from(new Set([...out.inputMethods, 'voice', 'gaze']));
  }
  out.situational = { preset, ...(expiresAt ? { expiresAt } : {}) };
  return SensoryProfileSchema.parse(out);
}
