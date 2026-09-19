import { describe, expect, it } from 'vitest';
import { fromHex } from '@sense/protocol';
import {
  MerkleLog,
  generateKeyPair,
  leafBytes,
  leafHash,
  verifyAppendOnly,
  verifyInclusion,
  verifyInclusionProof,
  verifySthSignature,
} from '../src';

async function makeLog(n: number) {
  const keys = await generateKeyPair();
  const log = new MerkleLog(keys, () => new Date('2026-01-15T10:00:00Z'));
  for (let i = 0; i < n; i++) log.append({ i, name: `agent-${i}` });
  return { keys, log };
}

describe('MerkleLog inclusion proofs', () => {
  it('verify for every leaf of every tree size 1..33', async () => {
    for (let n = 1; n <= 33; n++) {
      const { keys, log } = await makeLog(n);
      for (let i = 0; i < n; i++) {
        const proof = await log.inclusionProof(i);
        const res = await verifyInclusionProof({
          entry: { i, name: `agent-${i}` },
          proof,
          logPublicKey: keys.publicKey,
        });
        expect(res.ok, `n=${n} i=${i}: ${res.reason}`).toBe(true);
      }
    }
  });

  it('single-leaf tree has an empty path', async () => {
    const { log } = await makeLog(1);
    expect((await log.inclusionProof(0)).path).toEqual([]);
  });

  it('rejects out-of-range indices', async () => {
    const { log } = await makeLog(3);
    await expect(log.inclusionProof(3)).rejects.toThrow(RangeError);
    expect(
      verifyInclusion({
        leafHash: leafHash(leafBytes('x')),
        leafIndex: 5,
        treeSize: 3,
        path: [],
        rootHash: new Uint8Array(32),
      }),
    ).toBe(false);
  });
});

describe('MerkleLog tamper detection', () => {
  it('detects a modified entry', async () => {
    const { keys, log } = await makeLog(8);
    const proof = await log.inclusionProof(3);
    const res = await verifyInclusionProof({
      entry: { i: 3, name: 'agent-3-EVIL' },
      proof,
      logPublicKey: keys.publicKey,
    });
    expect(res.ok).toBe(false);
    expect(res.reason).toMatch(/not included/);
  });

  it('detects a modified proof path element', async () => {
    const { keys, log } = await makeLog(8);
    const proof = await log.inclusionProof(3);
    const bad = fromHex(proof.path[0] as string);
    bad[0] = (bad[0] ?? 0) ^ 0xff;
    const forged = {
      ...proof,
      path: [Array.from(bad, (b) => b.toString(16).padStart(2, '0')).join(''), ...proof.path.slice(1)],
    };
    expect(
      (
        await verifyInclusionProof({
          entry: { i: 3, name: 'agent-3' },
          proof: forged,
          logPublicKey: keys.publicKey,
        })
      ).ok,
    ).toBe(false);
  });

  it('detects a wrong leaf index', async () => {
    const { keys, log } = await makeLog(8);
    const proof = await log.inclusionProof(3);
    const res = await verifyInclusionProof({
      entry: { i: 3, name: 'agent-3' },
      proof: { ...proof, leafIndex: 4 },
      logPublicKey: keys.publicKey,
    });
    expect(res.ok).toBe(false);
  });

  it('detects a forged tree head root (signature no longer verifies)', async () => {
    const { keys, log } = await makeLog(8);
    const proof = await log.inclusionProof(2);
    const other = await makeLog(7); // different content, so a genuinely different root
    const forged = { ...proof, sth: { ...proof.sth, rootHash: other.log.rootAt() } };
    expect(forged.sth.rootHash).not.toBe(proof.sth.rootHash);
    expect(await verifySthSignature(forged.sth, keys.publicKey)).toBe(false);
    const res = await verifyInclusionProof({
      entry: { i: 2, name: 'agent-2' },
      proof: forged,
      logPublicKey: keys.publicKey,
    });
    expect(res.ok).toBe(false);
    expect(res.reason).toMatch(/signature/);
  });

  it('rejects a tree head signed by a different log key', async () => {
    const { log } = await makeLog(4);
    const attacker = await generateKeyPair();
    const proof = await log.inclusionProof(1);
    expect(
      (
        await verifyInclusionProof({
          entry: { i: 1, name: 'agent-1' },
          proof,
          logPublicKey: attacker.publicKey,
        })
      ).ok,
    ).toBe(false);
  });

  it('detects rewritten history against a pinned tree head (append-only)', async () => {
    const { log } = await makeLog(5);
    const pinned = await log.signedTreeHead();
    log.append({ i: 5, name: 'agent-5' });
    expect(verifyAppendOnly(pinned, log).ok).toBe(true);
    log.unsafeReplaceLeaf(2, { i: 2, name: 'rewritten' });
    const res = verifyAppendOnly(pinned, log);
    expect(res.ok).toBe(false);
    expect(res.reason).toMatch(/rewritten/);
  });

  it('detects a log that shrank', async () => {
    const big = await makeLog(6);
    const small = await makeLog(3);
    expect(verifyAppendOnly(await big.log.signedTreeHead(), small.log).ok).toBe(false);
  });

  it('a proof made for an earlier tree size still verifies against its own signed head', async () => {
    const { keys, log } = await makeLog(4);
    const old = await log.inclusionProof(1);
    log.append({ i: 4 });
    const res = await verifyInclusionProof({
      entry: { i: 1, name: 'agent-1' },
      proof: old,
      logPublicKey: keys.publicKey,
    });
    expect(res.ok).toBe(true);
  });
});
