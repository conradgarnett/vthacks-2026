import {
  ManualClock,
  type CapabilityId,
  type HelloRequest,
  type SenseCardCapability,
} from '@sense/protocol';
import {
  PublisherIdentity,
  SimulatedAnsClient,
  SimulatedRegistry,
  loadOrCreateAuthority,
  randomB64,
  type LiveChallenge,
  type VerificationResult,
  type AgentRecord,
} from '../src';

export const capability = (
  id: CapabilityId,
  over: Partial<SenseCardCapability> = {},
): SenseCardCapability => ({
  id,
  summary: `${id} capability`,
  basis: 'direct-sensor',
  freshness: { maxAgeSeconds: 60 },
  scopes: [`${id}:read`],
  safetyCritical: id === 'alarm-feed',
  ...over,
});

export async function makeWorld() {
  const clock = new ManualClock();
  const authority = await loadOrCreateAuthority(undefined, clock);
  const registry = new SimulatedRegistry(authority, clock);
  const client = new SimulatedAnsClient(registry, clock);
  const publish = (
    fqdn: string,
    over: Partial<Parameters<typeof PublisherIdentity.create>[0]> = {},
  ) =>
    PublisherIdentity.create({
      registry,
      fqdn,
      version: '1.0.0',
      name: fqdn,
      capabilities: [
        capability('alarm-feed'),
        capability('indoor-map', { basis: 'static', freshness: { maxAgeSeconds: null } }),
      ],
      covers: ['riverside'],
      clock,
      ...over,
    });
  return { clock, authority, registry, client, publish };
}

export function helloFor(sessionSuffix = 'a'): HelloRequest {
  return {
    v: 1,
    id: `h-${sessionSuffix}`,
    sessionId: `sess-${sessionSuffix}-12345678`,
    ts: '2026-01-15T10:00:00.000Z',
    type: 'hello',
    ephemeralPublicKey: randomB64(32),
    nonce: randomB64(24),
    protocols: ['sense/0.1'],
  };
}

export async function verifyLive(
  client: SimulatedAnsClient,
  pub: PublisherIdentity,
  record: AgentRecord = pub.record,
  mutate?: (c: LiveChallenge) => void,
): Promise<VerificationResult> {
  const req = helloFor();
  const ack = await pub.hello(req);
  const challenge: LiveChallenge = {
    fqdn: ack.fqdn,
    sessionId: req.sessionId,
    ephemeralPublicKey: req.ephemeralPublicKey,
    nonce: req.nonce,
    presentation: ack.presentation,
  };
  mutate?.(challenge);
  return client.verify(record, challenge);
}
