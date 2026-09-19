import { systemClock, type Clock } from '@sense/protocol';
import { importSpki, parseCertificate } from './crypto';
import { verifyAppendOnly, type InclusionProof } from './merkle';
import type { SimulatedRegistry } from './registry';
import { runVerification } from './verify';
import {
  NotConfiguredError,
  type AgentRecord,
  type AnsClient,
  type LiveChallenge,
  type SearchQuery,
  type VerificationResult,
} from './types';

/** Default client: a local, faithful-in-spirit model of ANS. Labelled "ANS-modeled" everywhere. */
export class SimulatedAnsClient implements AnsClient {
  readonly mode = 'simulated' as const;

  constructor(
    private readonly registry: SimulatedRegistry,
    private readonly clock: Clock = systemClock,
  ) {}

  async resolve(fqdn: string): Promise<AgentRecord> {
    return this.registry.resolve(fqdn);
  }

  async search(query: SearchQuery): Promise<AgentRecord[]> {
    return this.registry.search(query);
  }

  async getInclusionProof(record: AgentRecord): Promise<InclusionProof> {
    const proof = await this.registry.inclusionProof(record);
    if (!proof) throw new Error(`${record.fqdn} has no transparency-log entry`);
    return proof;
  }

  async verify(record: AgentRecord, challenge: LiveChallenge): Promise<VerificationResult> {
    const anchors = await this.registry.anchors();
    const result = await runVerification({
      record,
      challenge,
      serverRoot: parseCertificate(anchors.serverRootPem),
      identityRoot: parseCertificate(anchors.identityRootPem),
      logPublicKey: await importSpki(anchors.logPublicKey),
      now: new Date(this.clock.now()),
      fetchCrl: () => this.registry.crl(),
      fetchProof: (r) => this.registry.inclusionProof(r),
      checkAppendOnly: (sth) => {
        const pinned = this.registry.pinnedSth;
        if (pinned) {
          const c = verifyAppendOnly(pinned, this.registry.log);
          if (!c.ok) return c;
        }
        // Pin the newest head we have seen so later rewrites of history are detected.
        if (!pinned || sth.size >= pinned.size) this.registry.pinnedSth = sth;
        return { ok: true };
      },
      mode: 'simulated',
    });
    return result;
  }
}

/**
 * Live ANS client. Intentionally a typed stub: the public design pages read during the build did
 * not document a wire format precisely enough to implement honestly, and no recorded fixtures
 * exist. Every method throws NotConfigured; it never returns a fake "live" result.
 */
export class LiveAnsClient implements AnsClient {
  readonly mode = 'live' as const;

  constructor(readonly config: { registryUrl?: string } = {}) {}

  private notConfigured(op: string): never {
    throw new NotConfiguredError(
      `LiveAnsClient.${op}: live ANS is not configured. SENSE has no verified integration with a real ANS registry (see docs/ANS_NOTES.md). Use ANS_MODE=simulated.`,
    );
  }

  resolve(_fqdn: string): Promise<AgentRecord> {
    return Promise.resolve().then(() => this.notConfigured('resolve'));
  }
  verify(_record: AgentRecord, _challenge: LiveChallenge): Promise<VerificationResult> {
    return Promise.resolve().then(() => this.notConfigured('verify'));
  }
  getInclusionProof(_record: AgentRecord): Promise<InclusionProof> {
    return Promise.resolve().then(() => this.notConfigured('getInclusionProof'));
  }
  search(_query: SearchQuery): Promise<AgentRecord[]> {
    return Promise.resolve().then(() => this.notConfigured('search'));
  }
}

/** Choose the client for `ANS_MODE`. `live` returns the stub, which throws NotConfigured on use. */
export function createAnsClient(mode: string | undefined, registry: SimulatedRegistry, clock: Clock = systemClock): AnsClient {
  return mode === 'live' ? new LiveAnsClient() : new SimulatedAnsClient(registry, clock);
}
