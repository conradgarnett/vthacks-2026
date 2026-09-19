import 'reflect-metadata';
import * as x509 from '@peculiar/x509';
import { fromBase64, toBase64, toHex } from '@sense/protocol';

/** ECDSA P-256 / SHA-256 everywhere (WebCrypto). Signatures are raw r||s (IEEE P1363), base64. */
export const KEY_ALG = { name: 'ECDSA', namedCurve: 'P-256' } as const;
export const SIGN_ALG = { name: 'ECDSA', hash: 'SHA-256' } as const;
const CERT_ALG = { name: 'ECDSA', namedCurve: 'P-256', hash: 'SHA-256' } as const;

/** Private-arc placeholder OID (NOT assigned, NOT ANS's) carrying the agent code digest. */
export const DIGEST_EXTENSION_OID = '1.3.6.1.4.1.99999.1.1';

export type KeyPair = { privateKey: CryptoKey; publicKey: CryptoKey };

const subtle = globalThis.crypto.subtle;

export async function generateKeyPair(): Promise<KeyPair> {
  const pair = await subtle.generateKey(KEY_ALG, true, ['sign', 'verify']);
  return { privateKey: pair.privateKey, publicKey: pair.publicKey };
}

export async function signBytes(privateKey: CryptoKey, data: Uint8Array): Promise<string> {
  const sig = await subtle.sign(SIGN_ALG, privateKey, data as BufferSource);
  return toBase64(new Uint8Array(sig));
}

export async function verifyBytes(publicKey: CryptoKey, data: Uint8Array, signatureB64: string): Promise<boolean> {
  try {
    return await subtle.verify(SIGN_ALG, publicKey, fromBase64(signatureB64) as BufferSource, data as BufferSource);
  } catch {
    return false;
  }
}

export async function exportSpki(publicKey: CryptoKey): Promise<string> {
  return toBase64(new Uint8Array(await subtle.exportKey('spki', publicKey)));
}

export async function importSpki(spkiB64: string): Promise<CryptoKey> {
  return subtle.importKey('spki', fromBase64(spkiB64) as BufferSource, KEY_ALG, true, ['verify']);
}

export async function exportPkcs8Pem(privateKey: CryptoKey): Promise<string> {
  const b64 = toBase64(new Uint8Array(await subtle.exportKey('pkcs8', privateKey)));
  return `-----BEGIN PRIVATE KEY-----\n${b64.match(/.{1,64}/g)?.join('\n')}\n-----END PRIVATE KEY-----\n`;
}

export async function importPkcs8Pem(pem: string): Promise<CryptoKey> {
  const b64 = pem.replace(/-----[A-Z ]+-----/g, '').replace(/\s+/g, '');
  return subtle.importKey('pkcs8', fromBase64(b64) as BufferSource, KEY_ALG, true, ['sign']);
}

export async function sha256Hex(data: Uint8Array | string): Promise<string> {
  const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : data;
  return toHex(new Uint8Array(await subtle.digest('SHA-256', bytes as BufferSource)));
}

export function randomHex(bytes: number): string {
  return toHex(globalThis.crypto.getRandomValues(new Uint8Array(bytes)));
}

export function randomB64(bytes: number): string {
  return toBase64(globalThis.crypto.getRandomValues(new Uint8Array(bytes)));
}

// ── Certificates ─────────────────────────────────────────────────────────────────────────────

export interface CertOptions {
  serialNumber: string;
  commonName: string;
  notBefore: Date;
  notAfter: Date;
  /** DNS name SAN (the FQDN). */
  dns?: string;
  /** URI SAN, e.g. ans://v1.0.0.host */
  uri?: string;
  digest?: string;
}

export async function createRootCertificate(keys: KeyPair, name: string, notBefore: Date, notAfter: Date): Promise<x509.X509Certificate> {
  return x509.X509CertificateGenerator.createSelfSigned({
    serialNumber: randomHex(8),
    name: `CN=${name}`,
    notBefore,
    notAfter,
    signingAlgorithm: CERT_ALG,
    keys,
    extensions: [
      new x509.BasicConstraintsExtension(true, 0, true),
      new x509.KeyUsagesExtension(x509.KeyUsageFlags.keyCertSign | x509.KeyUsageFlags.cRLSign, true),
    ],
  });
}

export async function issueCertificate(
  issuer: { cert: x509.X509Certificate; keys: KeyPair },
  subjectPublicKey: CryptoKey,
  opts: CertOptions,
): Promise<x509.X509Certificate> {
  const names: x509.JsonGeneralName[] = [];
  if (opts.dns) names.push({ type: 'dns', value: opts.dns });
  if (opts.uri) names.push({ type: 'url', value: opts.uri });
  const extensions: x509.Extension[] = [
    new x509.BasicConstraintsExtension(false, undefined, true),
    new x509.KeyUsagesExtension(x509.KeyUsageFlags.digitalSignature, true),
  ];
  if (names.length > 0) extensions.push(new x509.SubjectAlternativeNameExtension(names));
  if (opts.digest) {
    extensions.push(new x509.Extension(DIGEST_EXTENSION_OID, false, new TextEncoder().encode(opts.digest)));
  }
  return x509.X509CertificateGenerator.create({
    serialNumber: opts.serialNumber,
    subject: `CN=${opts.commonName}`,
    issuer: issuer.cert.subject,
    notBefore: opts.notBefore,
    notAfter: opts.notAfter,
    signingKey: issuer.keys.privateKey,
    publicKey: subjectPublicKey,
    signingAlgorithm: CERT_ALG,
    extensions,
  });
}

export function parseCertificate(pem: string): x509.X509Certificate {
  return new x509.X509Certificate(pem);
}

export function certPem(cert: x509.X509Certificate): string {
  return cert.toString('pem');
}

export async function fingerprint(cert: x509.X509Certificate): Promise<string> {
  return sha256Hex(new Uint8Array(cert.rawData));
}

export function certDnsNames(cert: x509.X509Certificate): string[] {
  const san = cert.getExtension(x509.SubjectAlternativeNameExtension);
  return (san?.names.items ?? []).filter((n) => n.type === 'dns').map((n) => n.value);
}

export function certUriNames(cert: x509.X509Certificate): string[] {
  const san = cert.getExtension(x509.SubjectAlternativeNameExtension);
  return (san?.names.items ?? []).filter((n) => n.type === 'url').map((n) => n.value);
}

export function certDigest(cert: x509.X509Certificate): string | undefined {
  const ext = cert.getExtension(DIGEST_EXTENSION_OID);
  return ext ? new TextDecoder().decode(ext.value) : undefined;
}

/** Signature check + validity window at `date`. Does not check revocation or names. */
export async function certChainsTo(
  cert: x509.X509Certificate,
  root: x509.X509Certificate,
  date: Date,
): Promise<{ ok: boolean; reason?: string }> {
  if (cert.issuer !== root.subject) return { ok: false, reason: `issuer ${cert.issuer} is not ${root.subject}` };
  if (date < cert.notBefore) return { ok: false, reason: 'certificate is not yet valid' };
  if (date > cert.notAfter) return { ok: false, reason: 'certificate has expired' };
  const sigOk = await cert.verify({ publicKey: root.publicKey, signatureOnly: true });
  return sigOk ? { ok: true } : { ok: false, reason: 'signature does not verify against the root' };
}

export async function certPublicKey(cert: x509.X509Certificate): Promise<CryptoKey> {
  return cert.publicKey.export(CERT_ALG, ['verify']);
}
