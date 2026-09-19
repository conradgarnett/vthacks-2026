import { vi } from 'vitest';
import {
  getPersona,
  type AgentRequest,
  type AgentTransport,
  type SensoryProfile,
} from '@sense/protocol';
import { WorldSim } from '@sense/world-sim';
import { SenseBroker } from '../src';

export interface Harness {
  world: Awaited<ReturnType<typeof WorldSim.createManual>>;
  broker: SenseBroker;
  profile: SensoryProfile;
  /** Every request that left the broker, exactly as sent. */
  sent: { fqdn: string; msg: AgentRequest }[];
}

/** Wrap a transport so tests can see exactly what the broker sent. */
export function spyTransport(inner: AgentTransport, sent: Harness['sent']): AgentTransport {
  return {
    request: (fqdn, msg) => {
      sent.push({ fqdn, msg });
      return inner.request(fqdn, msg);
    },
    onPush: (fqdn, h) => inner.onPush(fqdn, h),
    onUnsolicited: (h) => inner.onUnsolicited(h),
  };
}

export async function makeHarness(
  opts: {
    persona?: Parameters<typeof getPersona>[0];
    allergens?: string[];
    wrapTransport?: (t: AgentTransport) => AgentTransport;
  } = {},
): Promise<Harness> {
  const world = await WorldSim.createManual();
  const profile = getPersona(opts.persona ?? 'blind');
  profile.allergens = opts.allergens ?? ['peanut'];
  const sent: Harness['sent'] = [];
  const base = spyTransport(world.transport, sent);
  const broker = new SenseBroker({
    ans: world.ansClient(),
    transport: opts.wrapTransport ? opts.wrapTransport(base) : base,
    clock: world.clock,
    getProfile: () => profile,
    getPose: () => ({ position: world.user.position, headingDeg: world.user.headingDeg }),
  });
  return { world, broker, profile, sent };
}

/** Let queued microtasks and async crypto finish. */
export async function settle(ms = 40): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

export const waitFor = vi.waitFor;
