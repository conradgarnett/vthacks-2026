import { createHash } from 'node:crypto';
import { canonicalJson, fromHex, toHex } from '@sense/protocol';
import { signBytes, verifyBytes, type KeyPair } from './crypto';

/**
 * RFC 6962-style Merkle tree (leaf = H(0x00 || data), node = H(0x01 || l || r)).
 * The identity package is Node-only (synchronous hashing via node:crypto).
 */
const sha256 = (...parts: Uint8Array[]): Uint8Array => {
  const h = createHash('sha256');
  for (const p of parts) h.update(p);
  return new Uint8Array(h.digest());
};

export const leafHash = (data: Uint8Array): Uint8Array => sha256(new Uint8Array([0]), data);
export const nodeHash = (l: Uint8Array, r: Uint8Array): Uint8Array => sha256(new Uint8Array([1]), l, r);

function largestPowerOfTwoBelow(n: number): number {
  let k = 1;
  while (k * 2 < n) k *= 2;
  return k;
}

function mth(hashes: Uint8Array[]): Uint8Array {
  if (hashes.length === 0) return sha256();
  if (hashes.length === 1) return hashes[0] as Uint8Array;
  const k = largestPowerOfTwoBelow(hashes.length);
  return nodeHash(mth(hashes.slice(0, k)), mth(hashes.slice(k)));
}

function path(m: number, hashes: Uint8Array[]): Uint8Array[] {
  if (hashes.length <= 1) return [];
  const k = largestPowerOfTwoBelow(hashes.length);
  return m < k ? [...path(m, hashes.slice(0, k)), mth(hashes.slice(k))] : [...path(m - k, hashes.slice(k)), mth(hashes.slice(0, k))];
}

/** RFC 9162 section 2.1.3.2 inclusion-proof verification. */
export function verifyInclusion(args: {
  leafHash: Uint8Array;
  leafIndex: number;
  treeSize: number;
  path: Uint8Array[];
  rootHash: Uint8Array;
}): boolean {
  const { leafIndex, treeSize } = args;
  if (!Number.isInteger(leafIndex) || leafIndex < 0 || leafIndex >= treeSize) return false;
  let fn = leafIndex;
  let sn = treeSize - 1;
  let r = args.leafHash;
  for (const p of args.path) {
    if (sn === 0) return false;
    if ((fn & 1) === 1 || fn === sn) {
      r = nodeHash(p, r);
      if ((fn & 1) === 0) {
        while ((fn & 1) === 0 && fn !== 0) {
          fn >>= 1;
          sn >>= 1;
        }
      }
    } else {
      r = nodeHash(r, p);
    }
    fn >>= 1;
    sn >>= 1;
  }
  return sn === 0 && toHex(r) === toHex(args.rootHash);
}

export interface SignedTreeHead {
  size: number;
  rootHash: string; // hex
  timestamp: string;
  signature: string; // base64, log key over canonical JSON of {size, rootHash, timestamp}
}

export interface InclusionProof {
  leafIndex: number;
  treeSize: number;
  path: string[]; // hex
  sth: SignedTreeHead;
}

const sthBytes = (s: Pick<SignedTreeHead, 'size' | 'rootHash' | 'timestamp'>): Uint8Array =>
  new TextEncoder().encode(canonicalJson({ size: s.size, rootHash: s.rootHash, timestamp: s.timestamp }));

export async function verifySthSignature(sth: SignedTreeHead, logPublicKey: CryptoKey): Promise<boolean> {
  return verifyBytes(logPublicKey, sthBytes(sth), sth.signature);
}

export function leafBytes(entry: unknown): Uint8Array {
  return new TextEncoder().encode(canonicalJson(entry));
}

/** Append-only log of registrations with signed tree heads. */
export class MerkleLog {
  private leaves: Uint8Array[] = [];

  constructor(
    private readonly keys: KeyPair,
    private readonly now: () => Date = () => new Date(),
  ) {}

  get size(): number {
    return this.leaves.length;
  }

  append(entry: unknown): number {
    this.leaves.push(leafHash(leafBytes(entry)));
    return this.leaves.length - 1;
  }

  rootAt(size: number = this.leaves.length): string {
    if (size < 0 || size > this.leaves.length) throw new RangeError('tree size out of range');
    return toHex(mth(this.leaves.slice(0, size)));
  }

  async signedTreeHead(): Promise<SignedTreeHead> {
    const base = { size: this.size, rootHash: this.rootAt(), timestamp: this.now().toISOString() };
    return { ...base, signature: await signBytes(this.keys.privateKey, sthBytes(base)) };
  }

  async inclusionProof(leafIndex: number): Promise<InclusionProof> {
    if (leafIndex < 0 || leafIndex >= this.size) throw new RangeError('leaf index out of range');
    return {
      leafIndex,
      treeSize: this.size,
      path: path(leafIndex, this.leaves).map(toHex),
      sth: await this.signedTreeHead(),
    };
  }

  /** TEST ONLY: rewrite history so tamper-detection can be exercised. */
  unsafeReplaceLeaf(index: number, entry: unknown): void {
    this.leaves[index] = leafHash(leafBytes(entry));
  }
}

/** Client side: verify a proof for `entry` against a log public key. */
export async function verifyInclusionProof(args: {
  entry: unknown;
  proof: InclusionProof;
  logPublicKey: CryptoKey;
}): Promise<{ ok: boolean; reason?: string }> {
  const { proof } = args;
  if (!(await verifySthSignature(proof.sth, args.logPublicKey))) {
    return { ok: false, reason: 'signed tree head signature is invalid' };
  }
  if (proof.sth.size !== proof.treeSize) return { ok: false, reason: 'proof size does not match tree head' };
  let path: Uint8Array[];
  try {
    path = proof.path.map(fromHex);
  } catch {
    return { ok: false, reason: 'proof path is malformed' };
  }
  const ok = verifyInclusion({
    leafHash: leafHash(leafBytes(args.entry)),
    leafIndex: proof.leafIndex,
    treeSize: proof.treeSize,
    path,
    rootHash: fromHex(proof.sth.rootHash),
  });
  return ok ? { ok: true } : { ok: false, reason: 'entry is not included under the signed root' };
}

/**
 * Simplified append-only check: the log's root at the previously seen size must equal the
 * previously pinned root. (RFC 6962 consistency proofs are on the roadmap.)
 */
export function verifyAppendOnly(previous: SignedTreeHead, log: MerkleLog): { ok: boolean; reason?: string } {
  if (log.size < previous.size) return { ok: false, reason: 'log shrank since the pinned tree head' };
  return log.rootAt(previous.size) === previous.rootHash
    ? { ok: true }
    : { ok: false, reason: 'history was rewritten since the pinned tree head' };
}
