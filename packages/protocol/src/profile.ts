import { z } from 'zod';
import { SENSES } from './percept';

export const MODALITIES = ['speech', 'spatial-audio', 'visual', 'haptic'] as const;
export const ModalitySchema = z.enum(MODALITIES);
export type Modality = z.infer<typeof ModalitySchema>;

export const INPUT_METHODS = ['touch', 'keyboard', 'switch', 'gaze', 'head', 'hand', 'voice'] as const;
export const InputMethodSchema = z.enum(INPUT_METHODS);
export type InputMethod = z.infer<typeof InputMethodSchema>;

export const PERSONA_IDS = ['blind', 'deaf', 'motor', 'anosmia', 'ageusia'] as const;
export type PersonaId = (typeof PERSONA_IDS)[number];

const UrgencyKeySchema = z.enum(['0', '1', '2', '3', '4']);
export const ModalitiesByUrgencySchema = z.record(UrgencyKeySchema, z.array(ModalitySchema).max(4));
export type ModalitiesByUrgency = z.infer<typeof ModalitiesByUrgencySchema>;

export const SITUATIONAL_PRESETS = ['hands-full', 'noisy-room'] as const;

/**
 * The sensory profile lives on the user's device only. It is never sent to a remote agent;
 * `@sense/core` enforces that at the outbound gate and in the invariants suite.
 */
export const SensoryProfileSchema = z.strictObject({
  id: z.string().min(1).max(64),
  name: z.string().min(1).max(80),
  personaId: z.string().max(32).optional(),
  /** Which senses SENSE should translate for this user. */
  translate: z.record(z.enum(SENSES), z.boolean()),
  /** Preferred output modalities per urgency (0..4). */
  output: ModalitiesByUrgencySchema,
  /** Optional per-sense overrides of `output`. */
  senseOverrides: z.partialRecord(z.enum(SENSES), z.partialRecord(UrgencyKeySchema, z.array(ModalitySchema).max(4))).optional(),
  verbosity: z.enum(['terse', 'normal', 'detailed']),
  speechRate: z.number().min(0.5).max(3),
  /** Percepts at or above this urgency interrupt whatever is currently being presented. */
  interruptFromUrgency: z.number().int().min(0).max(4),
  language: z.string().min(2).max(35),
  display: z.strictObject({
    highContrast: z.boolean(),
    textScale: z.number().min(1).max(2),
    reducedMotion: z.boolean(),
  }),
  inputMethods: z.array(InputMethodSchema).min(1).max(7),
  /** Declared allergens (local only), lower-case canonical names such as "peanut". */
  allergens: z.array(z.string().min(1).max(40)).max(32),
  /** Set when a situational preset is layered over the base profile. */
  situational: z
    .strictObject({
      preset: z.enum(SITUATIONAL_PRESETS),
      expiresAt: z.iso.datetime().optional(),
    })
    .optional(),
});
export type SensoryProfile = z.infer<typeof SensoryProfileSchema>;

/** Portable, signed profile file the user owns. */
export const SignedProfileSchema = z.strictObject({
  format: z.literal('sense-profile/0.1'),
  profile: SensoryProfileSchema,
  /** SPKI public key (base64) of the owner. */
  publicKey: z.string().max(400),
  /** ECDSA P-256 / SHA-256 signature (base64) over canonical JSON of `profile`. */
  signature: z.string().max(400),
});
export type SignedProfile = z.infer<typeof SignedProfileSchema>;
