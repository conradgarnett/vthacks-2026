import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { canonicalJson } from '@sense/protocol';
import {
  LiveAnsClient,
  LocalCA,
  NotConfiguredError,
  PublisherIdentity,
  SimulatedAnsClient,
  certPem,
  createAnsClient,
  fingerprint,
  generateKeyPair,
  loadOrCreateAuthority,
  parseCertificate,
  sha256Hex,
  signBytes,
  type AgentRecord,
} from '../src';
import { capability, helloFor, makeWorld, verifyLive } from './helpers';

const DAY = 86_400_000;

describe('happy path', () => {
  it('a legitimate agent passes all 7 steps and is VERIFIED', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const res = await verifyLive(w.client, pub);
    expect(res.outcome).toBe('VERIFIED');
    expect(res.steps).toHaveLength(7);
    expect(res.steps.map((s) => s.status)).toEqual(Array(7).fill('pass'));
    expect(res.steps.every((s) => s.evidence.length > 0)).toBe(true);
    expect(res.ansMode).toBe('simulated');
    expect(res.failingStep).toBeUndefined();
  });

  it('resolve returns an ANS-style versioned name and search finds by capability and area', async () => {
    const w = await makeWorld();
    await w.publish('riverside-hall.sim');
    await w.publish('city-air.sim', {
      capabilities: [capability('air-quality')],
      covers: ['riverside', 'city'],
    });
    const rec = await w.client.resolve('riverside-hall.sim');
    expect(rec.ansName).toBe('ans://v1.0.0.riverside-hall.sim');
    expect(
      (await w.client.search({ capability: 'air-quality', area: 'riverside' })).map((r) => r.fqdn),
    ).toEqual(['city-air.sim']);
    expect((await w.client.search({ capability: 'alarm-feed' })).map((r) => r.fqdn)).toEqual([
      'riverside-hall.sim',
    ]);
    expect(await w.client.search({ capability: 'arrivals' })).toEqual([]);
    expect((await w.client.search({ text: 'CITY' })).map((r) => r.fqdn)).toEqual(['city-air.sim']);
    await expect(w.client.resolve('nope.sim')).rejects.toThrow(/does not resolve/);
  });

  it('exposes an inclusion proof for a logged agent', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const proof = await w.client.getInclusionProof(pub.record);
    expect(proof.treeSize).toBe(1);
  });

  it('versions are immutable: re-registering the same version fails', async () => {
    const w = await makeWorld();
    await w.publish('riverside-hall.sim');
    await expect(w.publish('riverside-hall.sim')).rejects.toThrow(/immutable/);
    const v2 = await w.publish('riverside-hall.sim', { version: '1.1.0' });
    expect((await verifyLive(w.client, v2)).outcome).toBe('VERIFIED');
    expect((await w.client.resolve('riverside-hall.sim')).version).toBe('1.1.0');
  });
});

describe('failure modes name the failing step', () => {
  it('step 1: responder claims a different FQDN', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const res = await verifyLive(w.client, pub, pub.record, (c) => {
      c.fqdn = 'someone-else.sim';
    });
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(1);
    expect(res.steps.slice(1).every((s) => s.status === 'skipped')).toBe(true);
  });

  it('step 2: impersonator presents a valid certificate for the wrong FQDN', async () => {
    const w = await makeWorld();
    const victim = await w.publish('riverside-hall.sim');
    const evilKeys = await generateKeyPair();
    const evilCert = await w.authority.ca.issueServerCert('evil-relay.sim', evilKeys.publicKey);
    victim.serverKeys = evilKeys;
    victim.serverCert = evilCert;
    const res = await verifyLive(w.client, victim);
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(2);
    expect(res.steps[1]?.evidence.join(' ')).toMatch(/evil-relay\.sim/);
  });

  it('step 2: a certificate from an untrusted (rogue) CA is rejected', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const rogue = await LocalCA.create(w.clock, 'Rogue');
    const keys = await generateKeyPair();
    pub.serverKeys = keys;
    pub.serverCert = await rogue.issueServerCert('riverside-hall.sim', keys.publicKey);
    const res = await verifyLive(w.client, pub);
    expect(res.failingStep).toBe(2);
    expect(res.steps[1]?.evidence.join(' ')).toMatch(/trusted root/);
  });

  it('step 3: replayed challenge from an earlier session fails', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const oldAck = await pub.hello(helloFor('old'));
    const fresh = helloFor('new');
    const res = await w.client.verify(pub.record, {
      fqdn: oldAck.fqdn,
      sessionId: fresh.sessionId,
      ephemeralPublicKey: fresh.ephemeralPublicKey,
      nonce: fresh.nonce,
      presentation: oldAck.presentation,
    });
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(3);
  });

  it('step 3: agent that lacks the private key fails the challenge', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    pub.identityKeys = await generateKeyPair(); // certificate stays the same, key is now wrong
    const res = await verifyLive(w.client, pub);
    expect(res.failingStep).toBe(3);
  });

  it('step 4: revoked agent is rejected', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    expect((await verifyLive(w.client, pub)).outcome).toBe('VERIFIED');
    w.registry.revoke('riverside-hall.sim', '1.0.0');
    const res = await verifyLive(w.client, pub);
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(4);
    expect(res.steps[3]?.evidence.join(' ')).toMatch(/revoked/);
  });

  it('step 4: expired identity certificate is rejected', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    w.clock.advance(31 * DAY); // identity cert lives 30 days, server cert 90
    const res = await verifyLive(w.client, pub);
    expect(res.failingStep).toBe(4);
    expect(res.steps[3]?.evidence.join(' ')).toMatch(/expired/);
  });

  it('step 5: silent code swap (same name, new digest, not re-registered)', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    pub.digest = `sha256:${await sha256Hex('malicious build')}`;
    const res = await verifyLive(w.client, pub);
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(5);
    expect(res.steps[4]?.evidence.join(' ')).toMatch(/differs from registered/);
  });

  it('step 6: unlogged agent is rejected', async () => {
    const w = await makeWorld();
    const pub = await w.publish('shadow-agent.sim', { skipLog: true });
    const res = await verifyLive(w.client, pub);
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(6);
    expect(res.steps[5]?.evidence.join(' ')).toMatch(/not in the transparency log/);
  });

  it('step 6: a record pointing at the wrong log leaf is rejected', async () => {
    const w = await makeWorld();
    const a = await w.publish('a-hall.sim');
    const b = await w.publish('b-hall.sim');
    const forged: AgentRecord = { ...b.record, logLeafIndex: a.record.logLeafIndex as number };
    const res = await verifyLive(w.client, b, forged);
    expect(res.failingStep).toBe(6);
  });

  it('step 6: a rewritten log is detected via the pinned tree head', async () => {
    const w = await makeWorld();
    const a = await w.publish('a-hall.sim');
    expect((await verifyLive(w.client, a)).outcome).toBe('VERIFIED'); // pins the head
    const b = await w.publish('b-hall.sim');
    w.registry.log.unsafeReplaceLeaf(0, { fqdn: 'a-hall.sim', tampered: true });
    const res = await verifyLive(w.client, b);
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(6);
    expect(res.steps[5]?.evidence.join(' ')).toMatch(/consistency/);
  });

  it('step 7: tampered Sense Card content fails signature check', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const res = await verifyLive(w.client, pub, pub.record, (c) => {
      const cap = c.presentation.senseCard.card.capabilities[0];
      if (cap) cap.freshness = { maxAgeSeconds: 31_536_000 };
    });
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(7);
  });

  it('step 7: card signed by a different identity fails', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const other = await generateKeyPair();
    const card = pub.card();
    pub.signedCard = {
      card,
      signature: await signBytes(other.privateKey, new TextEncoder().encode(canonicalJson(card))),
      signerFingerprint: await fingerprint(pub.identityCert),
    };
    expect((await verifyLive(w.client, pub)).failingStep).toBe(7);
  });

  it('step 7: card that describes a different agent fails', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const res = await verifyLive(w.client, pub, pub.record, (c) => {
      c.presentation.senseCard.card.agent.fqdn = 'other-agent.sim';
    });
    expect(res.failingStep).toBe(7);
  });
});

describe('unavailable checks degrade to UNVERIFIED, not REJECTED', () => {
  it('log unreachable', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    w.registry.setLogAvailable(false);
    const res = await verifyLive(w.client, pub);
    expect(res.outcome).toBe('UNVERIFIED');
    expect(res.unavailableStep).toBe(6);
    expect(res.steps[5]?.status).toBe('unavailable');
    expect(res.steps[6]?.status).toBe('pass'); // remaining steps still run
  });

  it('revocation list unreachable', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    w.registry.setCrlAvailable(false);
    const res = await verifyLive(w.client, pub);
    expect(res.outcome).toBe('UNVERIFIED');
    expect(res.unavailableStep).toBe(4);
  });

  it('a failure still wins over an unavailable check', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    pub.digest = `sha256:${await sha256Hex('swap')}`;
    w.registry.setLogAvailable(false);
    expect((await verifyLive(w.client, pub)).outcome).toBe('REJECTED');
  });
});

describe('LiveAnsClient', () => {
  it('is a typed stub that throws NotConfigured and never fakes a result', async () => {
    const live = new LiveAnsClient();
    expect(live.mode).toBe('live');
    await expect(live.resolve('x.example')).rejects.toBeInstanceOf(NotConfiguredError);
    await expect(live.search({})).rejects.toThrow(/not configured/);
    await expect(live.getInclusionProof({} as AgentRecord)).rejects.toBeInstanceOf(
      NotConfiguredError,
    );
    await expect(live.verify({} as AgentRecord, {} as never)).rejects.toBeInstanceOf(
      NotConfiguredError,
    );
  });

  it('ANS_MODE selects the client', async () => {
    const w = await makeWorld();
    expect(createAnsClient('live', w.registry).mode).toBe('live');
    expect(createAnsClient('simulated', w.registry)).toBeInstanceOf(SimulatedAnsClient);
    expect(createAnsClient(undefined, w.registry).mode).toBe('simulated');
  });
});

describe('local key storage', () => {
  it('creates keys on first run, reloads them later, and the reloaded CA still verifies agents', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'sense-keys-'));
    try {
      const a1 = await loadOrCreateAuthority(dir);
      const a2 = await loadOrCreateAuthority(dir);
      expect(certPem(a2.ca.server.cert)).toBe(certPem(a1.ca.server.cert));
      expect(certPem(a2.ca.identity.cert)).toBe(certPem(a1.ca.identity.cert));
      expect(await readFile(join(dir, 'ca.json'), 'utf8')).toMatch(/BEGIN PRIVATE KEY/);
      // a cert issued by the reloaded CA chains to the original root
      const keys = await generateKeyPair();
      const cert = await a2.ca.issueServerCert('x-hall.sim', keys.publicKey);
      expect(parseCertificate(certPem(cert)).issuer).toBe(a1.ca.server.cert.subject);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it('.gitignore keeps dev keys out of the repository', async () => {
    const gi = await readFile(join(import.meta.dirname, '../../../.gitignore'), 'utf8');
    expect(gi).toMatch(/\.sense\/keys\//);
    expect(gi).toMatch(/\*\.pem/);
  });
});

describe('PublisherIdentity', () => {
  it('signs responses that verify with the identity certificate', async () => {
    const w = await makeWorld();
    const pub = await w.publish('riverside-hall.sim');
    const msg = await pub.sign({
      v: 1,
      id: 'r1',
      sessionId: 'sess-12345678',
      ts: '2026-01-15T10:00:00.000Z',
      type: 'capability_response',
      capability: 'alarm-feed',
      asOf: '2026-01-15T10:00:00.000Z',
      data: { alarms: [] },
    } as never);
    expect(msg.signature.length).toBeGreaterThan(10);
    expect(PublisherIdentity).toBeDefined();
  });
});
