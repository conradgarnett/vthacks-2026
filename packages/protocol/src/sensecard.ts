import { z } from 'zod';

export const CAPABILITY_IDS = [
  'indoor-map',
  'alarm-feed',
  'air-quality',
  'menu-allergens',
  'arrivals',
  'accessibility-features',
  'device-control',
] as const;
export const CapabilityIdSchema = z.enum(CAPABILITY_IDS);
export type CapabilityId = z.infer<typeof CapabilityIdSchema>;

export const FqdnSchema = z
  .string()
  .max(253)
  .regex(
    /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$/,
    'must be a lower-case fully qualified domain name',
  );

export const DigestSchema = z.string().regex(/^sha256:[0-9a-f]{64}$/, 'must be sha256:<64 hex>');

/** How the publisher obtained the data behind a capability. */
export const DATA_BASES = ['direct-sensor', 'staff-entered', 'inferred', 'static'] as const;
export const DataBasisSchema = z.enum(DATA_BASES);
export type DataBasis = z.infer<typeof DataBasisSchema>;

export const SenseCardCapabilitySchema = z.strictObject({
  id: CapabilityIdSchema,
  summary: z.string().min(1).max(200),
  basis: DataBasisSchema,
  freshness: z.strictObject({
    /** Data older than this is downgraded to UNVERIFIED by the trust engine. null = static data. */
    maxAgeSeconds: z.number().int().min(1).max(31_536_000).nullable(),
  }),
  /** Scopes a SENSE agent must request to use this capability (minimum necessary). */
  scopes: z.array(z.string().min(1).max(64)).min(1).max(8),
  /** True when this capability can affect physical safety. */
  safetyCritical: z.boolean(),
  safetyNote: z.string().max(300).optional(),
  /** Supports push subscriptions. */
  subscribable: z.boolean().optional(),
});
export type SenseCardCapability = z.infer<typeof SenseCardCapabilitySchema>;

/**
 * Sense Card: "alt text for physical reality". A publisher's machine-readable, signed description
 * of what its place or service can tell a SENSE agent. See docs/SENSE_CARD_SPEC.md.
 */
export const SenseCardSchema = z.strictObject({
  schema: z.literal('sense-card/0.1'),
  agent: z.strictObject({
    fqdn: FqdnSchema,
    version: z.string().min(1).max(32),
    /** Digest of the agent's code + metadata, pinned per version (immutability). */
    digest: DigestSchema,
    name: z.string().min(1).max(80),
    description: z.string().max(300).optional(),
    operator: z.string().max(120).optional(),
  }),
  /** Publisher-declared: this card describes a simulated place. Clients still label it themselves. */
  simulated: z.boolean().optional(),
  capabilities: z.array(SenseCardCapabilitySchema).min(1).max(16),
  issuedAt: z.iso.datetime(),
});
export type SenseCard = z.infer<typeof SenseCardSchema>;

export const SignedSenseCardSchema = z.strictObject({
  card: SenseCardSchema,
  /** ECDSA P-256 / SHA-256 signature (base64) over canonical JSON of `card`, by the identity key. */
  signature: z.string().min(1).max(400),
  /** SHA-256 fingerprint (hex) of the identity certificate that signed the card. */
  signerFingerprint: z.string().regex(/^[0-9a-f]{64}$/),
});
export type SignedSenseCard = z.infer<typeof SignedSenseCardSchema>;
