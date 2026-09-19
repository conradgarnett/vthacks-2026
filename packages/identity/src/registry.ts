import { systemClock, type CapabilityId, type Clock } from '@sense/protocol';
import { certPem, exportSpki, fingerprint } from './crypto';
import { ansName, type Authority, type SignedCrl, type TrustAnchors } from './ca';
import { MerkleLog, type InclusionProof, type SignedTreeHead } from './merkle';
import { LogUnavailableError, NotFoundError, type AgentRecord, type LogEntry, type SearchQuery } from './types';

export interface RegistrationRequest {
  fqdn: string;
  version: string;
  digest: string;
  displayName: string;
  serverPublicKey: CryptoKey;
  identityPublicKey: CryptoKey;
  capabilities: CapabilityId[];
  covers: string[];
}

export interface Registration {
  record: AgentRecord;
  serverCertPem: string;
  identityCertPem: string;
  identityCertSerial: string;
}

/**
 * Simulated registry + CA + transparency log. It stands in for ANS's Registration Authority.
 * It does NOT model ACME domain-control validation: registrations from the sim are trusted.
 */
export class SimulatedRegistry {
  readonly log: MerkleLog;
  private readonly records = new Map<string, AgentRecord[]>();
  private readonly serials = new Map<string, string>(); // `${fqdn}@${version}` -> identity cert serial
  private logUp = true;
  private crlUp = true;
  /** Pinned tree head for append-only checks. */
  pinnedSth: SignedTreeHead | undefined;

  constructor(
    readonly authority: Authority,
    private readonly clock: Clock = systemClock,
  ) {
    this.log = new MerkleLog(authority.logKeys, () => new Date(clock.now()));
  }

  async anchors(): Promise<TrustAnchors> {
    return {
      ...this.authority.ca.anchors(),
      logPublicKey: await exportSpki(this.authority.logKeys.publicKey),
    };
  }

  /** Register and seal the registration in the transparency log. */
  async register(req: RegistrationRequest): Promise<Registration> {
    return this.registerInternal(req, true);
  }

  /** Attacker helper: a registered agent that was never sealed into the log. */
  async registerUnlogged(req: RegistrationRequest): Promise<Registration> {
    return this.registerInternal(req, false);
  }

  private async registerInternal(req: RegistrationRequest, logged: boolean): Promise<Registration> {
    const existing = this.records.get(req.fqdn) ?? [];
    if (existing.some((r) => r.version === req.version)) {
      throw new Error(`version ${req.version} of ${req.fqdn} is already registered (versions are immutable)`);
    }
    const ca = this.authority.ca;
    const serverCert = await ca.issueServerCert(req.fqdn, req.serverPublicKey);
    const identityCert = await ca.issueIdentityCert({
      fqdn: req.fqdn,
      version: req.version,
      digest: req.digest,
      publicKey: req.identityPublicKey,
    });
    const identityFp = await fingerprint(identityCert);
    const entry: LogEntry = {
      fqdn: req.fqdn,
      version: req.version,
      digest: req.digest,
      identityCertFingerprint: identityFp,
    };
    const record: AgentRecord = {
      fqdn: req.fqdn,
      ansName: ansName(req.fqdn, req.version),
      version: req.version,
      digest: req.digest as AgentRecord['digest'],
      endpoints: [{ protocol: 'sense/0.1', url: `sim://${req.fqdn}` }],
      capabilities: req.capabilities,
      covers: req.covers,
      displayName: req.displayName,
      serverCertFingerprint: await fingerprint(serverCert),
      identityCertFingerprint: identityFp,
      ...(logged ? { logLeafIndex: this.log.append(entry) } : {}),
      registeredAt: new Date(this.clock.now()).toISOString(),
    };
    this.records.set(req.fqdn, [...existing, record]);
    this.serials.set(`${req.fqdn}@${req.version}`, identityCert.serialNumber);
    return {
      record,
      serverCertPem: certPem(serverCert),
      identityCertPem: certPem(identityCert),
      identityCertSerial: identityCert.serialNumber,
    };
  }

  /** Attacker helper: poison resolution so a name points at a record the registry never issued. */
  publishRecordUnchecked(record: AgentRecord): void {
    this.records.set(record.fqdn, [...(this.records.get(record.fqdn) ?? []), record]);
  }

  revoke(fqdn: string, version: string): void {
    const serial = this.serials.get(`${fqdn}@${version}`);
    if (!serial) throw new NotFoundError(`no identity certificate for ${fqdn}@${version}`);
    this.authority.ca.revoke(serial);
  }

  resolve(fqdn: string): AgentRecord {
    const versions = this.records.get(fqdn);
    const latest = versions?.[versions.length - 1];
    if (!latest) throw new NotFoundError(`${fqdn} does not resolve`);
    return latest;
  }

  search(q: SearchQuery): AgentRecord[] {
    const out: AgentRecord[] = [];
    for (const versions of this.records.values()) {
      const r = versions[versions.length - 1];
      if (!r) continue;
      if (q.capability && !r.capabilities.includes(q.capability)) continue;
      if (q.area && !r.covers.includes(q.area)) continue;
      if (q.text && !`${r.displayName} ${r.fqdn}`.toLowerCase().includes(q.text.toLowerCase())) continue;
      out.push(r);
    }
    return out.sort((a, b) => a.fqdn.localeCompare(b.fqdn));
  }

  /** Returns null when the registration was never logged. Throws when the log is down. */
  async inclusionProof(record: AgentRecord): Promise<InclusionProof | null> {
    if (!this.logUp) throw new LogUnavailableError();
    if (record.logLeafIndex === undefined) return null;
    return this.log.inclusionProof(record.logLeafIndex);
  }

  async crl(): Promise<SignedCrl> {
    if (!this.crlUp) throw new LogUnavailableError('revocation list is unreachable');
    return this.authority.ca.signedCrl();
  }

  /** Simulate outages so tests and the demo can show UNVERIFIED degradation. */
  setLogAvailable(up: boolean): void {
    this.logUp = up;
  }
  setCrlAvailable(up: boolean): void {
    this.crlUp = up;
  }
}
