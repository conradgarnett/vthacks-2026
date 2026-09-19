import type * as x509 from '@peculiar/x509';
import {
  canonicalJson,
  helloChallengeBytes,
  iso,
  signingPayload,
  systemClock,
  type CapabilityId,
  type Clock,
  type HelloAck,
  type HelloRequest,
  type CapabilityResponse,
  type PushMessage,
  type SenseCard,
  type SenseCardCapability,
  type SignedSenseCard,
} from '@sense/protocol';
import {
  certPem,
  fingerprint,
  generateKeyPair,
  parseCertificate,
  randomHex,
  sha256Hex,
  signBytes,
  type KeyPair,
} from './crypto';
import type { Registration, SimulatedRegistry } from './registry';
import type { AgentRecord } from './types';

export interface PublisherOptions {
  registry: SimulatedRegistry;
  fqdn: string;
  version: string;
  name: string;
  description?: string;
  capabilities: SenseCardCapability[];
  covers: string[];
  /** Code + metadata the digest is computed over. Change this to model a new version. */
  codeIdentity?: string;
  simulated?: boolean;
  clock?: Clock;
  /** Attacker helper: register without sealing the registration in the transparency log. */
  skipLog?: boolean;
}

/**
 * A publisher agent's identity: keys, certificates, signed Sense Card, and the ability to answer
 * `hello` and sign responses. Used by world-sim publishers and the `create-sense-agent` template.
 * Fields are mutable on purpose so attacker agents can present forged material.
 */
export class PublisherIdentity {
  serverCert!: x509.X509Certificate;
  identityCert!: x509.X509Certificate;
  signedCard!: SignedSenseCard;
  registration!: Registration;

  private constructor(
    readonly fqdn: string,
    public version: string,
    public digest: string,
    public serverKeys: KeyPair,
    public identityKeys: KeyPair,
    private readonly clock: Clock,
    readonly opts: PublisherOptions,
  ) {}

  static async create(opts: PublisherOptions): Promise<PublisherIdentity> {
    const clock = opts.clock ?? systemClock;
    const digest = `sha256:${await sha256Hex(opts.codeIdentity ?? `${opts.fqdn}@${opts.version}`)}`;
    const pub = new PublisherIdentity(
      opts.fqdn,
      opts.version,
      digest,
      await generateKeyPair(),
      await generateKeyPair(),
      clock,
      opts,
    );
    const req = {
      fqdn: opts.fqdn,
      version: opts.version,
      digest,
      displayName: opts.name,
      serverPublicKey: pub.serverKeys.publicKey,
      identityPublicKey: pub.identityKeys.publicKey,
      capabilities: opts.capabilities.map((c) => c.id as CapabilityId),
      covers: opts.covers,
    };
    pub.registration = opts.skipLog
      ? await opts.registry.registerUnlogged(req)
      : await opts.registry.register(req);
    pub.serverCert = parseCertificate(pub.registration.serverCertPem);
    pub.identityCert = parseCertificate(pub.registration.identityCertPem);
    pub.signedCard = await pub.signCard();
    return pub;
  }

  get record(): AgentRecord {
    return this.registration.record;
  }

  card(): SenseCard {
    return {
      schema: 'sense-card/0.1',
      agent: {
        fqdn: this.fqdn,
        version: this.version,
        digest: this.digest as SenseCard['agent']['digest'],
        name: this.opts.name,
        ...(this.opts.description ? { description: this.opts.description } : {}),
      },
      ...(this.opts.simulated !== undefined ? { simulated: this.opts.simulated } : {}),
      capabilities: this.opts.capabilities,
      issuedAt: iso(this.clock),
    };
  }

  async signCard(): Promise<SignedSenseCard> {
    const card = this.card();
    return {
      card,
      signature: await signBytes(
        this.identityKeys.privateKey,
        new TextEncoder().encode(canonicalJson(card)),
      ),
      signerFingerprint: await fingerprint(this.identityCert),
    };
  }

  /** Answer the SENSE agent's hello: present certificates and sign the fresh challenge. */
  async hello(req: HelloRequest): Promise<HelloAck> {
    const bytes = helloChallengeBytes({
      nonce: req.nonce,
      sessionId: req.sessionId,
      ephemeralPublicKey: req.ephemeralPublicKey,
      fqdn: this.fqdn,
    });
    return {
      v: 1,
      id: `ack-${randomHex(4)}`,
      sessionId: req.sessionId,
      ts: iso(this.clock),
      type: 'hello_ack',
      fqdn: this.fqdn,
      nonce: req.nonce,
      presentation: {
        serverCertPem: certPem(this.serverCert),
        identityCertPem: certPem(this.identityCert),
        agentVersion: this.version,
        digest: this.digest as SenseCard['agent']['digest'],
        senseCard: this.signedCard,
        challengeSignatureIdentity: await signBytes(this.identityKeys.privateKey, bytes),
        challengeSignatureServer: await signBytes(this.serverKeys.privateKey, bytes),
      },
    };
  }

  /** Sign a `capability_response` or `push` body with the identity key. */
  async sign<T extends CapabilityResponse | PushMessage>(msg: Omit<T, 'signature'>): Promise<T> {
    const signature = await signBytes(
      this.identityKeys.privateKey,
      signingPayload({ ...msg, signature: '' } as T),
    );
    return { ...msg, signature } as T;
  }
}
