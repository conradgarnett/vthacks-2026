import { z } from 'zod';
import { MAX_SHORT_WORDS, countWords } from './util';

export const SENSES = ['vision', 'hearing', 'touch', 'smell', 'taste'] as const;
export const SenseSchema = z.enum(SENSES);
export type Sense = z.infer<typeof SenseSchema>;

export const KINDS = ['alert', 'description', 'answer', 'status', 'action'] as const;
export const PerceptKindSchema = z.enum(KINDS);
export type PerceptKind = z.infer<typeof PerceptKindSchema>;

export const TIERS = ['VERIFIED', 'INFERRED', 'UNVERIFIED', 'REJECTED'] as const;
export const TierSchema = z.enum(TIERS);
export type Tier = z.infer<typeof TierSchema>;

/** 0 ambient, 1 low, 2 normal, 3 high, 4 life-safety. */
export const UrgencySchema = z.number().int().min(0).max(4);
export type Urgency = z.infer<typeof UrgencySchema>;

export const SpatialSchema = z.strictObject({
  /** Clockwise from the user's facing direction, 0..360 (0 = straight ahead). */
  bearingDeg: z.number().min(0).max(360),
  elevationDeg: z.number().min(-90).max(90).optional(),
  distanceM: z.number().min(0).max(100_000).optional(),
  clockPosition: z.number().int().min(1).max(12).optional(),
});
export type Spatial = z.infer<typeof SpatialSchema>;

export const ProvenanceSchema = z.strictObject({
  tier: TierSchema,
  /** Agent FQDN, or a local source such as "device-camera". */
  source: z.string().min(1).max(253),
  /** Short human-readable name for the source, spoken in safety percepts. */
  sourceLabel: z.string().min(1).max(60).optional(),
  agentVersion: z.string().max(64).optional(),
  verifiedAt: z.iso.datetime().optional(),
  confidence: z.number().min(0).max(1).optional(),
  /** Human-readable verification steps or the inference basis. */
  evidence: z.array(z.string().max(300)).max(32),
});
export type Provenance = z.infer<typeof ProvenanceSchema>;

export const PerceptActionSchema = z.strictObject({
  id: z.string().min(1).max(64),
  label: z.string().min(1).max(60),
});
export type PerceptAction = z.infer<typeof PerceptActionSchema>;

export const PerceptSchema = z
  .strictObject({
    id: z.string().min(1).max(64),
    timestamp: z.iso.datetime(),
    sense: SenseSchema,
    kind: PerceptKindSchema,
    urgency: UrgencySchema,
    /** Speakable in about two seconds at alert speech rate. */
    short: z.string().min(1).max(120),
    long: z.string().max(2000).optional(),
    spatial: SpatialSchema.optional(),
    provenance: ProvenanceSchema,
    actions: z.array(PerceptActionSchema).max(6).optional(),
    /** True when this percept can affect physical safety (alarm, hazard, allergen, air quality). */
    safety: z.boolean().optional(),
    /** True when the world-sim is the source; the UI shows a persistent SIMULATED WORLD badge. */
    simulated: z.boolean().optional(),
    /** Percepts answering the same question share a group id; each keeps its own provenance. */
    groupId: z.string().max(64).optional(),
    /** How many identical percepts were collapsed into this one. */
    count: z.number().int().min(1).optional(),
  })
  .superRefine((p, ctx) => {
    if (countWords(p.short) > MAX_SHORT_WORDS) {
      ctx.addIssue({
        code: 'custom',
        path: ['short'],
        message: `short must be at most ${MAX_SHORT_WORDS} words so it is speakable in ~2 s`,
      });
    }
    if (p.safety) {
      const lower = p.short.toLowerCase();
      const label = (p.provenance.sourceLabel ?? p.provenance.source).toLowerCase();
      if (!lower.includes(p.provenance.tier.toLowerCase())) {
        ctx.addIssue({
          code: 'custom',
          path: ['short'],
          message: 'safety percepts must state the provenance tier in the primary message',
        });
      }
      if (!lower.includes(label)) {
        ctx.addIssue({
          code: 'custom',
          path: ['short'],
          message: 'safety percepts must name the source in the primary message',
        });
      }
    }
  });
export type Percept = z.infer<typeof PerceptSchema>;

/** A rejected input is never information; it is surfaced only as a security event. */
export const SecurityEventSchema = z.strictObject({
  id: z.string().min(1).max(64),
  timestamp: z.iso.datetime(),
  kind: z.enum([
    'IDENTITY_REJECTED',
    'CONTENT_REJECTED',
    'INJECTION_NEUTRALIZED',
    'SIGNATURE_INVALID',
    'STALE_DOWNGRADED',
    'SOURCE_OFFLINE',
    'SOURCE_CONFLICT',
    'RATE_LIMITED',
    'SPOOF_SUPPRESSION_BLOCKED',
  ]),
  source: z.string().max(253),
  /** The verification step that failed, when the event comes from the identity pipeline. */
  failingStep: z.number().int().min(1).max(7).optional(),
  failingStepName: z.string().max(80).optional(),
  message: z.string().max(400),
});
export type SecurityEvent = z.infer<typeof SecurityEventSchema>;
