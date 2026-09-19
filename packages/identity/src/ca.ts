import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type * as x509 from '@peculiar/x509';
import { canonicalJson, systemClock, type Clock } from '@sense/protocol';
import {
  certPem,
  createRootCertificate,
  exportPkcs8Pem,
  exportSpki,
  importSpki,
  generateKeyPair,
  importPkcs8Pem,
  issueCertificate,
  parseCertificate,
  randomHex,
  signBytes,
  verifyBytes,
  certPublicKey,
  type KeyPair,
} from './crypto';

const DAY = 86_400_000;

export interface Issuer {
  cert: x509.X509Certificate;
  keys: KeyPair;
}

export interface TrustAnchors {
  serverRootPem: string;
  identityRootPem: string;
  /** SPKI (base64) of the transparency log's signing key. */
  logPublicKey: string;
}

export interface SignedCrl {
  revoked: string[];
  issuedAt: string;
  signature: string;
}

/**
 * Local development CA modeling ANS's dual-certificate design: a "server" CA issues the
 * long-lived FQDN-bound server certificate, an "identity" CA issues per-version identity
 * certificates bound to a code digest. Keys are generated locally and never leave the machine.
 */
export class LocalCA {
  private readonly revokedSerials = new Set<string>();

  private constructor(
    readonly server: Issuer,
    readonly identity: Issuer,
    private readonly clock: Clock,
  ) {}

  static async create(clock: Clock = systemClock, name = 'SENSE Simulated'): Promise<LocalCA> {
    const nb = new Date(clock.now() - DAY);
    const na = new Date(clock.now() + 3650 * DAY);
    const serverKeys = await generateKeyPair();
    const identityKeys = await generateKeyPair();
    return new LocalCA(
      {
        cert: await createRootCertificate(serverKeys, `${name} Server CA`, nb, na),
        keys: serverKeys,
      },
      {
        cert: await createRootCertificate(identityKeys, `${name} Identity CA`, nb, na),
        keys: identityKeys,
      },
      clock,
    );
  }

  async issueServerCert(fqdn: string, publicKey: CryptoKey): Promise<x509.X509Certificate> {
    return issueCertificate(this.server, publicKey, {
      serialNumber: randomHex(10),
      commonName: fqdn,
      dns: fqdn,
      notBefore: new Date(this.clock.now() - DAY),
      notAfter: new Date(this.clock.now() + 90 * DAY),
    });
  }

  async issueIdentityCert(args: {
    fqdn: string;
    version: string;
    digest: string;
    publicKey: CryptoKey;
  }): Promise<x509.X509Certificate> {
    return issueCertificate(this.identity, args.publicKey, {
      serialNumber: randomHex(10),
      commonName: `${args.fqdn}@${args.version}`,
      dns: args.fqdn,
      uri: ansName(args.fqdn, args.version),
      digest: args.digest,
      notBefore: new Date(this.clock.now() - DAY),
      notAfter: new Date(this.clock.now() + 30 * DAY),
    });
  }

  revoke(serialNumber: string): void {
    this.revokedSerials.add(serialNumber.toLowerCase());
  }

  async signedCrl(): Promise<SignedCrl> {
    const body = {
      revoked: [...this.revokedSerials].sort(),
      issuedAt: new Date(this.clock.now()).toISOString(),
    };
    return { ...body, signature: await signBytes(this.identity.keys.privateKey, crlBytes(body)) };
  }

  anchors(): Pick<TrustAnchors, 'serverRootPem' | 'identityRootPem'> {
    return {
      serverRootPem: certPem(this.server.cert),
      identityRootPem: certPem(this.identity.cert),
    };
  }

  async export(): Promise<StoredCa> {
    return {
      serverRootPem: certPem(this.server.cert),
      serverKeyPem: await exportPkcs8Pem(this.server.keys.privateKey),
      identityRootPem: certPem(this.identity.cert),
      identityKeyPem: await exportPkcs8Pem(this.identity.keys.privateKey),
    };
  }

  static async import(stored: StoredCa, clock: Clock = systemClock): Promise<LocalCA> {
    const restore = async (pem: string, keyPem: string): Promise<Issuer> => {
      const cert = parseCertificate(pem);
      const privateKey = await importPkcs8Pem(keyPem);
      return { cert, keys: { privateKey, publicKey: await certPublicKey(cert) } };
    };
    return new LocalCA(
      await restore(stored.serverRootPem, stored.serverKeyPem),
      await restore(stored.identityRootPem, stored.identityKeyPem),
      clock,
    );
  }
}

export interface StoredCa {
  serverRootPem: string;
  serverKeyPem: string;
  identityRootPem: string;
  identityKeyPem: string;
}

const crlBytes = (body: { revoked: string[]; issuedAt: string }): Uint8Array =>
  new TextEncoder().encode(canonicalJson(body));

export async function verifyCrl(
  crl: SignedCrl,
  identityRoot: x509.X509Certificate,
): Promise<boolean> {
  const { signature, ...body } = crl;
  return verifyBytes(await certPublicKey(identityRoot), crlBytes(body), signature);
}

/** `ans://v{version}.{fqdn}`, the versioned name form used in the ANS draft. */
export function ansName(fqdn: string, version: string): string {
  return `ans://v${version}.${fqdn}`;
}

// ── Local key storage (gitignored directory) ────────────────────────────────────────────────

export interface Authority {
  ca: LocalCA;
  logKeys: KeyPair;
}

/**
 * Load the dev CA and log key from `dir`, creating and saving them on first run. With no `dir`
 * (tests) everything stays in memory. Keys live in a gitignored directory (`.sense/keys`).
 */
export async function loadOrCreateAuthority(
  dir: string | undefined,
  clock: Clock = systemClock,
): Promise<Authority> {
  if (!dir) return { ca: await LocalCA.create(clock), logKeys: await generateKeyPair() };
  const caFile = join(dir, 'ca.json');
  const logFile = join(dir, 'log.json');
  try {
    const stored = JSON.parse(await readFile(caFile, 'utf8')) as StoredCa;
    const log = JSON.parse(await readFile(logFile, 'utf8')) as {
      privateKeyPem: string;
      publicKey: string;
    };
    return {
      ca: await LocalCA.import(stored, clock),
      logKeys: {
        privateKey: await importPkcs8Pem(log.privateKeyPem),
        publicKey: await importSpki(log.publicKey),
      },
    };
  } catch {
    const ca = await LocalCA.create(clock);
    const logKeys = await generateKeyPair();
    await mkdir(dir, { recursive: true });
    await writeFile(caFile, JSON.stringify(await ca.export(), null, 2), { mode: 0o600 });
    await writeFile(
      logFile,
      JSON.stringify(
        {
          privateKeyPem: await exportPkcs8Pem(logKeys.privateKey),
          publicKey: await exportSpki(logKeys.publicKey),
        },
        null,
        2,
      ),
      { mode: 0o600 },
    );
    return { ca, logKeys };
  }
}
