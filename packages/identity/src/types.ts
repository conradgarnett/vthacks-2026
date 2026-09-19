import { z } from 'zod';
import {
  CapabilityIdSchema,
  DigestSchema,
  FqdnSchema,
  type CapabilityId,
  type Presentation,
} from '@sense/protocol';
import type { InclusionProof } from './merkle';

export const AgentRecordSchema = z.strictObject({
  fqdn: FqdnSchema,
  /** `ans://v{version}.{fqdn}` */
  ansName: z.string().max(300),
  version: z.string().min(1).max(32),
  /** Digest registered for this version. Immutable once registered. */
  digest: DigestSchema,
  /** Where to reach the agent. In the simulation this is `sim://{fqdn}`. */
  endpoints: z
    .array(z.strictObject({ protocol: z.string().max(32), url: z.string().max(300) }))
    .min(1)
    .max(4),
  capabilities: z.array(CapabilityIdSchema).max(16),
  /** Areas this agent speaks for, for location-aware discovery (e.g. "riverside", "city"). */
  covers: z.array(z.string().max(64)).max(16),
  displayName: z.string().max(80),
  serverCertFingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  identityCertFingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  /** Index in the transparency log, if the registration was logged. */
  logLeafIndex: z.number().int().min(0).optional(),
  registeredAt: z.iso.datetime(),
});
export type AgentRecord = z.infer<typeof AgentRecordSchema>;

/** The entry the transparency log seals for each registration. */
export interface LogEntry {
  fqdn: string;
  version: string;
  digest: string;
  identityCertFingerprint: string;
}

export const STEP_NAMES = [
  'Name resolves and record is well-formed',
  'Server certificate chains to a trusted root and matches the FQDN',
  'Live challenge-response proves control of the private key',
  'Identity certificate is valid, unexpired and unrevoked',
  'Agent version digest matches the registered version',
  'Registration has a valid transparency-log inclusion proof',
  'Sense Card is signed by the same identity and is schema-valid',
] as const;

export type StepStatus = 'pass' | 'fail' | 'unavailable' | 'skipped';

export interface VerificationStep {
  step: number; // 1..7
  name: string;
  status: StepStatus;
  evidence: string[];
}

export type VerificationOutcome = 'VERIFIED' | 'UNVERIFIED' | 'REJECTED';

export interface VerificationResult {
  fqdn: string;
  outcome: VerificationOutcome;
  steps: VerificationStep[];
  /** First failing step, when outcome is REJECTED. */
  failingStep?: number;
  failingStepName?: string;
  /** First unavailable step, when outcome is UNVERIFIED because a check could not run. */
  unavailableStep?: number;
  verifiedAt: string;
  ansMode: 'simulated' | 'live';
}

/** What the SENSE agent sent in `hello` plus what the live agent presented in `hello_ack`. */
export interface LiveChallenge {
  /** FQDN the responding agent claims (from hello_ack). Must equal the resolved record's FQDN. */
  fqdn: string;
  sessionId: string;
  ephemeralPublicKey: string;
  nonce: string;
  presentation: Presentation;
}

export interface SearchQuery {
  capability?: CapabilityId;
  /** Area label the user is in, e.g. "riverside". Matches records whose `covers` includes it. */
  area?: string;
  text?: string;
}

/** Identity and discovery, modeled on the Agent Name Service. */
export interface AnsClient {
  readonly mode: 'simulated' | 'live';
  resolve(fqdn: string): Promise<AgentRecord>;
  verify(record: AgentRecord, challenge: LiveChallenge): Promise<VerificationResult>;
  getInclusionProof(record: AgentRecord): Promise<InclusionProof>;
  search(query: SearchQuery): Promise<AgentRecord[]>;
}

export class NotConfiguredError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NotConfigured';
  }
}

export class NotFoundError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NotFound';
  }
}

export class LogUnavailableError extends Error {
  constructor(message = 'transparency log is unreachable') {
    super(message);
    this.name = 'LogUnavailable';
  }
}
