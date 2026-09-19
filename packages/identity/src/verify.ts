import type * as x509 from '@peculiar/x509';
import { SignedSenseCardSchema, canonicalJson, helloChallengeBytes } from '@sense/protocol';
import {
  certChainsTo,
  certDigest,
  certDnsNames,
  certPublicKey,
  certUriNames,
  fingerprint,
  parseCertificate,
  verifyBytes,
} from './crypto';
import { ansName, verifyCrl, type SignedCrl } from './ca';
import { verifyInclusionProof, type InclusionProof, type SignedTreeHead } from './merkle';
import {
  AgentRecordSchema,
  STEP_NAMES,
  type AgentRecord,
  type LiveChallenge,
  type LogEntry,
  type StepStatus,
  type VerificationResult,
  type VerificationStep,
} from './types';

export interface VerifyContext {
  record: AgentRecord;
  challenge: LiveChallenge;
  serverRoot: x509.X509Certificate;
  identityRoot: x509.X509Certificate;
  logPublicKey: CryptoKey;
  now: Date;
  fetchCrl(): Promise<SignedCrl>;
  /** null = agent is not in the log. Throws = log unreachable. */
  fetchProof(record: AgentRecord): Promise<InclusionProof | null>;
  /** Optional pinned-tree-head consistency check (simplified append-only verification). */
  checkAppendOnly?(sth: SignedTreeHead): { ok: boolean; reason?: string };
  mode: 'simulated' | 'live';
}

type StepOutcome = { status: Exclude<StepStatus, 'skipped'>; evidence: string[] };
const pass = (...evidence: string[]): StepOutcome => ({ status: 'pass', evidence });
const fail = (...evidence: string[]): StepOutcome => ({ status: 'fail', evidence });
const unavailable = (...evidence: string[]): StepOutcome => ({ status: 'unavailable', evidence });

/**
 * The ordered verification pipeline. The first failing step rejects the agent; later steps are
 * reported as skipped. A step that cannot run (log or revocation list unreachable) yields
 * UNVERIFIED rather than REJECTED, and the remaining steps still run.
 */
export async function runVerification(ctx: VerifyContext): Promise<VerificationResult> {
  const { record, challenge } = ctx;
  const steps: VerificationStep[] = [];
  let serverCert: x509.X509Certificate | undefined;
  let identityCert: x509.X509Certificate | undefined;

  const checks: Array<() => Promise<StepOutcome>> = [
    // 1. Name resolves and record is well-formed
    async () => {
      const parsed = AgentRecordSchema.safeParse(record);
      if (!parsed.success)
        return fail(`record is malformed: ${parsed.error.issues[0]?.message ?? 'invalid'}`);
      if (record.ansName !== ansName(record.fqdn, record.version)) {
        return fail(
          `record name ${record.ansName} does not match ${ansName(record.fqdn, record.version)}`,
        );
      }
      if (challenge.fqdn !== record.fqdn) {
        return fail(`responder claims ${challenge.fqdn}, but the record is for ${record.fqdn}`);
      }
      return pass(
        `${record.fqdn} resolved to ${record.ansName}`,
        `endpoint ${record.endpoints[0]?.url ?? '?'}`,
      );
    },
    // 2. Server certificate chains to a trusted root and matches the FQDN
    async () => {
      try {
        serverCert = parseCertificate(challenge.presentation.serverCertPem);
      } catch {
        return fail('server certificate could not be parsed');
      }
      const chain = await certChainsTo(serverCert, ctx.serverRoot, ctx.now);
      if (!chain.ok)
        return fail(`server certificate does not chain to a trusted root: ${chain.reason}`);
      const names = certDnsNames(serverCert);
      if (!names.includes(record.fqdn)) {
        return fail(
          `server certificate is for ${names.join(', ') || 'no name'}, not ${record.fqdn}`,
        );
      }
      if ((await fingerprint(serverCert)) !== record.serverCertFingerprint) {
        return fail('server certificate is not the one registered for this name');
      }
      return pass(
        'chains to the trusted server root',
        `name matches ${record.fqdn}`,
        'matches registered fingerprint',
      );
    },
    // 3. Live challenge-response
    async () => {
      try {
        identityCert = parseCertificate(challenge.presentation.identityCertPem);
      } catch {
        return fail('identity certificate could not be parsed');
      }
      if (!serverCert) return fail('no server certificate to check the challenge against');
      const bytes = helloChallengeBytes({
        nonce: challenge.nonce,
        sessionId: challenge.sessionId,
        ephemeralPublicKey: challenge.ephemeralPublicKey,
        fqdn: record.fqdn,
      });
      const idOk = await verifyBytes(
        await certPublicKey(identityCert),
        bytes,
        challenge.presentation.challengeSignatureIdentity,
      );
      const srvOk = await verifyBytes(
        await certPublicKey(serverCert),
        bytes,
        challenge.presentation.challengeSignatureServer,
      );
      if (!idOk) return fail('identity key did not sign this session’s fresh challenge');
      if (!srvOk) return fail('server key did not sign this session’s fresh challenge');
      return pass('fresh nonce signed by both the identity key and the server key');
    },
    // 4. Identity certificate valid, unexpired, unrevoked
    async () => {
      if (!identityCert) return fail('no identity certificate');
      const chain = await certChainsTo(identityCert, ctx.identityRoot, ctx.now);
      if (!chain.ok) return fail(`identity certificate is invalid: ${chain.reason}`);
      if (!certDnsNames(identityCert).includes(record.fqdn))
        return fail('identity certificate is for a different name');
      if (!certUriNames(identityCert).includes(record.ansName)) {
        return fail(`identity certificate does not carry ${record.ansName}`);
      }
      if ((await fingerprint(identityCert)) !== record.identityCertFingerprint) {
        return fail('identity certificate is not the one registered for this version');
      }
      let crl: SignedCrl;
      try {
        crl = await ctx.fetchCrl();
      } catch {
        return unavailable(
          'valid and unexpired, but the revocation list is unreachable so revocation was not checked',
        );
      }
      if (!(await verifyCrl(crl, ctx.identityRoot)))
        return fail('revocation list signature is invalid');
      if (crl.revoked.includes(identityCert.serialNumber.toLowerCase())) {
        return fail(`identity certificate ${identityCert.serialNumber} has been revoked`);
      }
      return pass(
        'valid, unexpired, not on the signed revocation list',
        `version ${record.version}`,
      );
    },
    // 5. Agent version digest matches the registered version
    async () => {
      const p = challenge.presentation;
      if (p.agentVersion !== record.version)
        return fail(`running version ${p.agentVersion}, registered ${record.version}`);
      if (p.digest !== record.digest) {
        return fail(
          `running code digest ${p.digest.slice(0, 19)}… differs from registered ${record.digest.slice(0, 19)}…`,
        );
      }
      const bound = identityCert ? certDigest(identityCert) : undefined;
      if (bound !== record.digest)
        return fail('identity certificate is bound to a different code digest');
      return pass(
        `digest ${record.digest.slice(0, 19)}… matches the registered, immutable version`,
      );
    },
    // 6. Transparency-log inclusion proof
    async () => {
      if (!identityCert) return fail('no identity certificate');
      let proof: InclusionProof | null;
      try {
        proof = await ctx.fetchProof(record);
      } catch {
        return unavailable('transparency log is unreachable, so inclusion was not checked');
      }
      if (!proof) return fail('registration is not in the transparency log');
      const entry: LogEntry = {
        fqdn: record.fqdn,
        version: record.version,
        digest: record.digest,
        identityCertFingerprint: await fingerprint(identityCert),
      };
      const res = await verifyInclusionProof({ entry, proof, logPublicKey: ctx.logPublicKey });
      if (!res.ok) return fail(`inclusion proof rejected: ${res.reason}`);
      if (ctx.checkAppendOnly) {
        const c = ctx.checkAppendOnly(proof.sth);
        if (!c.ok) return fail(`log consistency check failed: ${c.reason}`);
      }
      return pass(
        `entry ${proof.leafIndex} included under signed tree head of size ${proof.treeSize}`,
      );
    },
    // 7. Sense Card signed by the same identity and schema-valid
    async () => {
      const parsed = SignedSenseCardSchema.safeParse(challenge.presentation.senseCard);
      if (!parsed.success)
        return fail(
          `Sense Card is not schema-valid: ${parsed.error.issues[0]?.message ?? 'invalid'}`,
        );
      const { card, signature, signerFingerprint } = parsed.data;
      if (
        card.agent.fqdn !== record.fqdn ||
        card.agent.version !== record.version ||
        card.agent.digest !== record.digest
      ) {
        return fail('Sense Card describes a different agent name, version or digest');
      }
      if (!identityCert) return fail('no identity certificate');
      if (signerFingerprint !== (await fingerprint(identityCert)))
        return fail('Sense Card was signed by a different identity');
      const ok = await verifyBytes(
        await certPublicKey(identityCert),
        new TextEncoder().encode(canonicalJson(card)),
        signature,
      );
      return ok
        ? pass('schema-valid and signed by this agent’s identity key')
        : fail('Sense Card signature does not verify');
    },
  ];

  let failed: VerificationStep | undefined;
  let firstUnavailable: VerificationStep | undefined;
  for (const [i, check] of checks.entries()) {
    const step = i + 1;
    const name = STEP_NAMES[i] as string;
    if (failed) {
      steps.push({
        step,
        name,
        status: 'skipped',
        evidence: [`skipped because step ${failed.step} failed`],
      });
      continue;
    }
    let outcome: StepOutcome;
    try {
      outcome = await check();
    } catch (err) {
      outcome = fail(`check raised an error: ${err instanceof Error ? err.message : 'unknown'}`);
    }
    const s: VerificationStep = { step, name, ...outcome };
    steps.push(s);
    if (s.status === 'fail') failed = s;
    else if (s.status === 'unavailable' && !firstUnavailable) firstUnavailable = s;
  }

  const base = {
    fqdn: record.fqdn,
    steps,
    verifiedAt: ctx.now.toISOString(),
    ansMode: ctx.mode,
  } as const;
  if (failed)
    return { ...base, outcome: 'REJECTED', failingStep: failed.step, failingStepName: failed.name };
  if (firstUnavailable)
    return { ...base, outcome: 'UNVERIFIED', unavailableStep: firstUnavailable.step };
  return { ...base, outcome: 'VERIFIED' };
}
