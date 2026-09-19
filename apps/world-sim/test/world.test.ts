import { describe, expect, it } from 'vitest';
import {
  AgentResponseSchema,
  HelloAckSchema,
  PAYLOAD_SCHEMAS,
  PushSchema,
  type AgentRequest,
  type CapabilityId,
} from '@sense/protocol';
import { randomB64, type LiveChallenge, type VerificationResult } from '@sense/identity';
import { ATTACKERS, HONEST_FQDNS, WorldSim, buildWorldSimHttp } from '../src';

let n = 0;
const base = () => ({
  v: 1 as const,
  id: `t-${++n}`,
  sessionId: 'sess-test-12345678',
  ts: '2026-01-15T10:00:00.000Z',
});

/** What the broker does on connect: hello over the transport, then verify. */
async function connect(world: WorldSim, fqdn: string): Promise<VerificationResult> {
  const record = await world.ansClient().resolve(fqdn);
  const hello = {
    ...base(),
    type: 'hello' as const,
    ephemeralPublicKey: randomB64(32),
    nonce: randomB64(24),
    protocols: ['sense/0.1'],
  };
  const ack = HelloAckSchema.parse(await world.transport.request(fqdn, hello));
  const challenge: LiveChallenge = {
    fqdn: ack.fqdn,
    sessionId: hello.sessionId,
    ephemeralPublicKey: hello.ephemeralPublicKey,
    nonce: hello.nonce,
    presentation: ack.presentation,
  };
  return world.ansClient().verify(record, challenge);
}

const query = (
  capability: CapabilityId,
  params?: Record<string, string | number | boolean>,
): AgentRequest => ({
  ...base(),
  type: 'capability_query',
  capability,
  scope: [capability === 'device-control' ? 'lift:call' : `${capability}:read`],
  ...(params ? { params } : {}),
});

describe('honest publishers', () => {
  it('every honest agent is reachable at its own name and passes all 7 verification steps', async () => {
    const world = await WorldSim.createManual();
    for (const fqdn of HONEST_FQDNS) {
      const res = await connect(world, fqdn);
      expect(res.outcome, fqdn).toBe('VERIFIED');
      expect(res.steps.every((s) => s.status === 'pass')).toBe(true);
    }
  });

  it('serves schema-valid payloads for every capability it declares', async () => {
    const world = await WorldSim.createManual();
    for (const fqdn of HONEST_FQDNS) {
      const agent = world.agents.get(fqdn);
      for (const c of agent?.identity.card().capabilities ?? []) {
        const res = AgentResponseSchema.parse(await world.transport.request(fqdn, query(c.id)));
        expect(res.type, `${fqdn}/${c.id}`).toBe('capability_response');
        if (res.type === 'capability_response') {
          expect(() => PAYLOAD_SCHEMAS[c.id].parse(res.data)).not.toThrow();
        }
      }
    }
  });

  it('cards mark safety-critical capabilities and declare freshness', async () => {
    const world = await WorldSim.createManual();
    const hall = world.agents.get('riverside-hall.sim')?.identity.card();
    const alarm = hall?.capabilities.find((c) => c.id === 'alarm-feed');
    expect(alarm?.safetyCritical).toBe(true);
    expect(alarm?.freshness.maxAgeSeconds).toBe(30);
    expect(hall?.simulated).toBe(true);
    expect(hall?.agent.name).toMatch(/simulated/);
  });

  it('enforces minimum scopes and unknown capabilities', async () => {
    const world = await WorldSim.createManual();
    const denied = AgentResponseSchema.parse(
      await world.transport.request('riverside-hall.sim', {
        ...query('alarm-feed'),
        scope: ['alarm-feed:read', 'profile:read'],
      } as AgentRequest),
    );
    expect(denied).toMatchObject({ type: 'error', code: 'scope_denied' });
    const notOffered = AgentResponseSchema.parse(
      await world.transport.request('riverside-hall.sim', query('arrivals')),
    );
    expect(notOffered).toMatchObject({ type: 'error', code: 'scope_denied' });
  });

  it('rejects malformed requests (extra fields such as a profile are not accepted)', async () => {
    const world = await WorldSim.createManual();
    const bad = {
      ...query('alarm-feed'),
      profile: { allergens: ['peanut'] },
    } as unknown as AgentRequest;
    expect(await world.transport.request('riverside-hall.sim', bad)).toMatchObject({
      type: 'error',
      code: 'bad_request',
    });
  });

  it('unknown and offline agents are unreachable', async () => {
    const world = await WorldSim.createManual();
    await expect(world.transport.request('nowhere.sim', query('alarm-feed'))).rejects.toThrow(
      /does not resolve/,
    );
    world.setOffline('metro-transit.sim', true);
    await expect(world.transport.request('metro-transit.sim', query('arrivals'))).rejects.toThrow(
      /unreachable/,
    );
  });
});

describe('subscriptions and events', () => {
  it('subscribe sends the current state, then pushes signed updates on change; unsubscribe stops them', async () => {
    const world = await WorldSim.createManual();
    const pushes: unknown[] = [];
    world.transport.onPush('riverside-hall.sim', (raw) => pushes.push(raw));
    const ack = AgentResponseSchema.parse(
      await world.transport.request('riverside-hall.sim', {
        ...base(),
        type: 'subscribe',
        capability: 'alarm-feed',
        scope: ['alarm-feed:read'],
      }),
    );
    expect(ack.type).toBe('subscribe_ack');
    await Promise.resolve();
    await Promise.resolve();
    await world.trigger('fire-alarm');
    const parsed = pushes.map((p) => PushSchema.parse(p));
    expect(parsed.length).toBeGreaterThanOrEqual(2);
    expect(parsed[0]?.seq).toBeLessThan(parsed[parsed.length - 1]?.seq ?? 0);
    expect(JSON.stringify(parsed[parsed.length - 1]?.data)).toMatch(/"state":"active"/);

    if (ack.type !== 'subscribe_ack') throw new Error('unreachable');
    const un = AgentResponseSchema.parse(
      await world.transport.request('riverside-hall.sim', {
        ...base(),
        type: 'unsubscribe',
        subscriptionId: ack.subscriptionId,
      }),
    );
    expect(un.type).toBe('unsubscribe_ack');
    const before = pushes.length;
    await world.trigger('clear-alarm');
    expect(pushes.length).toBe(before);
  });

  it('a timeline fires events at set times on a manual clock and sends heartbeats', async () => {
    const world = await WorldSim.createManual();
    const pushes: unknown[] = [];
    world.transport.onPush('riverside-hall.sim', (raw) => pushes.push(raw));
    await world.transport.request('riverside-hall.sim', {
      ...base(),
      type: 'subscribe',
      capability: 'air-quality',
      scope: ['air-quality:read'],
    });
    await Promise.resolve();
    await Promise.resolve();
    world.play('smoke');
    await world.tick();
    world.clock.advance(20_000);
    await world.tick();
    world.clock.advance(20_000);
    await world.tick();
    const res = AgentResponseSchema.parse(
      await world.transport.request('riverside-hall.sim', query('air-quality')),
    );
    const data =
      res.type === 'capability_response'
        ? PAYLOAD_SCHEMAS['air-quality'].parse(res.data)
        : undefined;
    expect(data?.readings.find((r) => r.sensorId === 'smoke-east')?.value).toBe(6.2);
    expect(pushes.length).toBeGreaterThan(3);
  });

  it('frozen sensors serve old measuredAt while the agent is still verified (stale data)', async () => {
    const world = await WorldSim.createManual();
    await world.trigger('smoke', 3);
    world.freezeSensors();
    const frozenAt = world.clock.now();
    world.clock.advance(5 * 60_000);
    const res = AgentResponseSchema.parse(
      await world.transport.request('riverside-hall.sim', query('air-quality')),
    );
    const data =
      res.type === 'capability_response'
        ? PAYLOAD_SCHEMAS['air-quality'].parse(res.data)
        : undefined;
    expect(Date.parse(data?.readings[0]?.measuredAt ?? '')).toBe(frozenAt);
    expect((await connect(world, 'riverside-hall.sim')).outcome).toBe('VERIFIED');
  });

  it('the lift can be called, and refuses during an alarm', async () => {
    const world = await WorldSim.createManual();
    const call = { action: 'call', device: 'lift-1', floor: 2 };
    const ok = AgentResponseSchema.parse(
      await world.transport.request('hall-lifts.sim', query('device-control', call)),
    );
    expect(
      ok.type === 'capability_response' &&
        PAYLOAD_SCHEMAS['device-control'].parse(ok.data).result?.ok,
    ).toBe(true);
    await world.trigger('fire-alarm');
    const refused = AgentResponseSchema.parse(
      await world.transport.request('hall-lifts.sim', query('device-control', call)),
    );
    expect(
      refused.type === 'capability_response' &&
        PAYLOAD_SCHEMAS['device-control'].parse(refused.data).result?.ok,
    ).toBe(false);
  });

  it('is deterministic for a given seed (readings and timeline)', async () => {
    const run = async () => {
      const w = await WorldSim.createManual(7);
      const seq: unknown[] = [];
      for (let i = 0; i < 5; i++) {
        const r = AgentResponseSchema.parse(
          await w.transport.request('city-air.sim', query('air-quality')),
        );
        seq.push(
          r.type === 'capability_response'
            ? PAYLOAD_SCHEMAS['air-quality'].parse(r.data).readings.map((x) => x.value)
            : null,
        );
        w.clock.advance(1000);
      }
      return seq;
    };
    expect(await run()).toEqual(await run());
  });
});

describe('attackers (section 5.4)', () => {
  it('are absent until activated, then discoverable', async () => {
    const world = await WorldSim.createManual();
    const client = world.ansClient();
    expect(
      (await client.search({ capability: 'alarm-feed', area: 'riverside' })).map((r) => r.fqdn),
    ).toEqual(['riverside-hall.sim']);
    await world.activateAttackers();
    const names = (await client.search({ capability: 'alarm-feed', area: 'riverside' })).map(
      (r) => r.fqdn,
    );
    expect(names).toEqual(
      expect.arrayContaining([
        'riverside-hall.sim',
        ATTACKERS.impersonator,
        ATTACKERS.spoofer,
        ATTACKERS.flooder,
      ]),
    );
    await world.activateAttackers(); // idempotent
  });

  it.each([
    ['impersonator', 2, /alerts-relay\.sim/],
    ['revoked', 4, /revoked/],
    ['codeSwap', 5, /differs from registered/],
    ['unlogged', 6, /not in the transparency log/],
    ['spoofer', 2, /trusted root/],
  ] as const)('%s is REJECTED at step %i', async (id, step, evidence) => {
    const world = await WorldSim.createManual();
    await world.activateAttackers();
    const res = await connect(world, ATTACKERS[id]);
    expect(res.outcome).toBe('REJECTED');
    expect(res.failingStep).toBe(step);
    expect(res.steps[step - 1]?.evidence.join(' ')).toMatch(evidence);
  });

  it('the prompt-injector and the flooder have VERIFIED identities (identity is not trust in content)', async () => {
    const world = await WorldSim.createManual();
    await world.activateAttackers();
    expect((await connect(world, ATTACKERS.injector)).outcome).toBe('VERIFIED');
    expect((await connect(world, ATTACKERS.flooder)).outcome).toBe('VERIFIED');
    const kiosk = AgentResponseSchema.parse(
      await world.transport.request(ATTACKERS.injector, query('accessibility-features')),
    );
    expect(JSON.stringify(kiosk)).toMatch(/IGNORE ALL PREVIOUS RULES/);
  });

  it('the spoofer publishes an unsolicited "false alarm" push', async () => {
    const world = await WorldSim.createManual();
    const seen: { fqdn: string; raw: unknown }[] = [];
    world.transport.onUnsolicited((fqdn, raw) => seen.push({ fqdn, raw }));
    await world.trigger('fire-alarm');
    await world.trigger('spoof-false-alarm');
    expect(seen).toHaveLength(1);
    expect(seen[0]?.fqdn).toBe(ATTACKERS.spoofer);
    expect(JSON.stringify(seen[0]?.raw)).toMatch(/False alarm, ignore/);
  });

  it('the flooder pushes many alerts to subscribers', async () => {
    const world = await WorldSim.createManual();
    await world.activateAttackers(['flooder']);
    let count = 0;
    world.transport.onPush(ATTACKERS.flooder, () => count++);
    await world.transport.request(ATTACKERS.flooder, {
      ...base(),
      type: 'subscribe',
      capability: 'alarm-feed',
      scope: ['alarm-feed:read'],
    });
    await Promise.resolve();
    await Promise.resolve();
    await world.trigger('flood', 120);
    expect(count).toBeGreaterThanOrEqual(120);
  });
});

describe('HTTP view of the world ("domains")', () => {
  it('lists hostnames, serves each agent’s signed Sense Card by Host header, and answers RPC by path', async () => {
    const world = await WorldSim.createManual();
    const app = await buildWorldSimHttp(world);
    const root = (await app.inject({ method: 'GET', url: '/' })).json() as {
      label: string;
      hostnames: string[];
    };
    expect(root.label).toBe('SIMULATED WORLD');
    expect(root.hostnames).toEqual([...HONEST_FQDNS].sort());

    const card = await app.inject({
      method: 'GET',
      url: '/.well-known/sense-card.json',
      headers: { host: 'bella-cucina.sim' },
    });
    expect(card.statusCode).toBe(200);
    expect(card.json().card.agent.fqdn).toBe('bella-cucina.sim');
    expect(
      (
        await app.inject({
          method: 'GET',
          url: '/.well-known/sense-card.json',
          headers: { host: 'nope.sim' },
        })
      ).statusCode,
    ).toBe(404);

    const rpc = await app.inject({
      method: 'POST',
      url: '/agents/metro-transit.sim/rpc',
      payload: query('arrivals'),
    });
    expect(AgentResponseSchema.parse(rpc.json()).type).toBe('capability_response');
    const byHost = await app.inject({
      method: 'POST',
      url: '/rpc',
      headers: { host: 'metro-transit.sim:8788' },
      payload: query('arrivals'),
    });
    expect(byHost.statusCode).toBe(200);

    world.setOffline('metro-transit.sim', true);
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/agents/metro-transit.sim/rpc',
          payload: query('arrivals'),
        })
      ).statusCode,
    ).toBe(503);

    expect((await app.inject({ method: 'POST', url: '/sim/event/fire-alarm' })).statusCode).toBe(
      200,
    );
    expect(
      (await app.inject({ method: 'POST', url: '/sim/event/does-not-exist' })).statusCode,
    ).toBe(400);
    await app.close();
  });
});
