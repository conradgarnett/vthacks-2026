import { z } from 'zod';
import { CapabilityIdSchema, FqdnSchema, SignedSenseCardSchema, DigestSchema } from './sensecard';
import { canonicalJson } from './util';

export const PROTOCOL_VERSION = 'sense/0.1';

const base = {
  v: z.literal(1),
  id: z.string().min(1).max(64),
  /** Ephemeral per-session identifier chosen by the SENSE agent. Carries no user identity. */
  sessionId: z.string().min(8).max(64),
  ts: z.iso.datetime(),
};

const scopeSchema = z.array(z.string().min(1).max(64)).min(1).max(8);
const paramsSchema = z
  .record(z.string().max(40), z.union([z.string().max(120), z.number(), z.boolean()]))
  .refine((r) => Object.keys(r).length <= 8, 'at most 8 params');

// ── Requests (SENSE agent -> publisher agent) ────────────────────────────────────────────────

export const HelloRequestSchema = z.strictObject({
  ...base,
  type: z.literal('hello'),
  ephemeralPublicKey: z.string().min(1).max(400),
  nonce: z.string().min(16).max(128),
  protocols: z.array(z.string().max(32)).max(4),
});

export const CapabilityQuerySchema = z.strictObject({
  ...base,
  type: z.literal('capability_query'),
  capability: CapabilityIdSchema,
  scope: scopeSchema,
  params: paramsSchema.optional(),
});

export const SubscribeRequestSchema = z.strictObject({
  ...base,
  type: z.literal('subscribe'),
  capability: CapabilityIdSchema,
  scope: scopeSchema,
});

export const UnsubscribeRequestSchema = z.strictObject({
  ...base,
  type: z.literal('unsubscribe'),
  subscriptionId: z.string().min(1).max(64),
});

export const AgentRequestSchema = z.discriminatedUnion('type', [
  HelloRequestSchema,
  CapabilityQuerySchema,
  SubscribeRequestSchema,
  UnsubscribeRequestSchema,
]);
export type AgentRequest = z.infer<typeof AgentRequestSchema>;
export type HelloRequest = z.infer<typeof HelloRequestSchema>;
export type CapabilityQuery = z.infer<typeof CapabilityQuerySchema>;
export type SubscribeRequest = z.infer<typeof SubscribeRequestSchema>;
export type UnsubscribeRequest = z.infer<typeof UnsubscribeRequestSchema>;

// ── Responses (publisher agent -> SENSE agent) ───────────────────────────────────────────────

export const PresentationSchema = z.strictObject({
  serverCertPem: z.string().max(8000),
  identityCertPem: z.string().max(8000),
  agentVersion: z.string().max(32),
  digest: DigestSchema,
  senseCard: SignedSenseCardSchema,
  /** Signature (base64) over the hello challenge by the identity key. */
  challengeSignatureIdentity: z.string().max(400),
  /** Signature (base64) over the hello challenge by the server key. */
  challengeSignatureServer: z.string().max(400),
});
export type Presentation = z.infer<typeof PresentationSchema>;

export const HelloAckSchema = z.strictObject({
  ...base,
  type: z.literal('hello_ack'),
  fqdn: FqdnSchema,
  nonce: z.string().min(16).max(128),
  presentation: PresentationSchema,
});

export const CapabilityResponseSchema = z.strictObject({
  ...base,
  type: z.literal('capability_response'),
  capability: CapabilityIdSchema,
  asOf: z.iso.datetime(),
  /** Validated against the capability-specific schema in payloads.ts. */
  data: z.unknown(),
  signature: z.string().min(1).max(400),
});

export const SubscribeAckSchema = z.strictObject({
  ...base,
  type: z.literal('subscribe_ack'),
  subscriptionId: z.string().min(1).max(64),
  capability: CapabilityIdSchema,
});

export const UnsubscribeAckSchema = z.strictObject({
  ...base,
  type: z.literal('unsubscribe_ack'),
  subscriptionId: z.string().min(1).max(64),
});

export const PushSchema = z.strictObject({
  ...base,
  type: z.literal('push'),
  subscriptionId: z.string().min(1).max(64),
  capability: CapabilityIdSchema,
  seq: z.number().int().min(0),
  asOf: z.iso.datetime(),
  data: z.unknown(),
  signature: z.string().min(1).max(400),
});

export const ErrorMessageSchema = z.strictObject({
  ...base,
  type: z.literal('error'),
  code: z.enum(['bad_request', 'unsupported', 'scope_denied', 'not_found', 'internal']),
  message: z.string().max(200),
});

export const AgentResponseSchema = z.discriminatedUnion('type', [
  HelloAckSchema,
  CapabilityResponseSchema,
  SubscribeAckSchema,
  UnsubscribeAckSchema,
  PushSchema,
  ErrorMessageSchema,
]);
export type AgentResponse = z.infer<typeof AgentResponseSchema>;
export type HelloAck = z.infer<typeof HelloAckSchema>;
export type CapabilityResponse = z.infer<typeof CapabilityResponseSchema>;
export type SubscribeAck = z.infer<typeof SubscribeAckSchema>;
export type UnsubscribeAck = z.infer<typeof UnsubscribeAckSchema>;
export type PushMessage = z.infer<typeof PushSchema>;
export type ErrorMessage = z.infer<typeof ErrorMessageSchema>;

/** Bytes a publisher signs for `capability_response` and `push`: canonical JSON minus the signature. */
export function signingPayload(msg: CapabilityResponse | PushMessage): Uint8Array {
  const { signature: _signature, ...rest } = msg;
  return new TextEncoder().encode(canonicalJson(rest));
}

/** Bytes both identity and server keys sign to prove live control of the private key. */
export function helloChallengeBytes(args: {
  nonce: string;
  sessionId: string;
  ephemeralPublicKey: string;
  fqdn: string;
}): Uint8Array {
  return new TextEncoder().encode(
    `${PROTOCOL_VERSION}|hello|${args.fqdn}|${args.nonce}|${args.sessionId}|${args.ephemeralPublicKey}`,
  );
}
