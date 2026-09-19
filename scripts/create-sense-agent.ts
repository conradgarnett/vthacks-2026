import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  CAPABILITY_IDS,
  FqdnSchema,
  PAYLOAD_SCHEMAS,
  SenseCardCapabilitySchema,
  type CapabilityId,
  type SenseCardCapability,
} from '@sense/protocol';

/**
 * create-sense-agent: scaffold a minimal publisher agent for the SIMULATED registry.
 *
 *   npm run create-sense-agent -- my-cafe.sim --name "My Cafe" --capability menu-allergens --covers riverside
 *
 * It writes agents/<fqdn>/{agent.ts, sense-card.json, run.ts, README.md}. `run.ts` registers the agent,
 * runs the same 7-step verification SENSE runs, and prints the result. It does NOT publish anything to a
 * real registry: SENSE's identity layer is ANS-modeled and simulated.
 */

export interface AgentSpec {
  fqdn: string;
  name: string;
  capabilities: CapabilityId[];
  covers: string[];
}

/** Example payloads. Each one is validated against the real schema by the generator's own test. */
export const EXAMPLE_PAYLOADS: Record<CapabilityId, unknown> = {
  arrivals: { stop: 'Example stop', arrivals: [{ route: '1', destination: 'Town centre', etaMinutes: 5, accessible: true }] },
  'accessibility-features': { features: [{ kind: 'step-free-route', location: 'Main entrance', notes: 'Ramp on the left.' }] },
  'menu-allergens': {
    restaurant: 'Example Cafe',
    items: [
      {
        id: 'soup',
        name: 'Tomato soup',
        ingredients: ['tomato', 'cream'],
        allergens: { contains: ['milk'], mayContain: [] },
        preparation: 'Simmered daily.',
        spice: 0,
      },
    ],
  },
  'indoor-map': {
    floor: 'G',
    nodes: [
      { id: 'door', kind: 'exit', label: 'Front door', x: 0, y: 0 },
      { id: 'hall', kind: 'room', label: 'Main room', x: 0, y: 5 },
    ],
    edges: [{ from: 'door', to: 'hall', meters: 5 }],
  },
  'alarm-feed': { alarms: [] },
  'air-quality': { readings: [] },
  'device-control': { devices: [] },
};

const SAFETY: CapabilityId[] = ['alarm-feed', 'air-quality', 'menu-allergens'];

/** Sensible Sense Card capability defaults per capability (the "Sense Card generator"). */
export function capabilityPreset(id: CapabilityId): SenseCardCapability {
  const dynamic = id === 'alarm-feed' || id === 'air-quality' || id === 'arrivals' || id === 'device-control';
  return SenseCardCapabilitySchema.parse({
    id,
    summary: `${id} for this place`,
    basis: dynamic ? 'direct-sensor' : id === 'indoor-map' ? 'static' : 'staff-entered',
    freshness: { maxAgeSeconds: id === 'alarm-feed' ? 30 : dynamic ? 60 : id === 'menu-allergens' ? 86_400 : null },
    scopes: [id === 'device-control' ? 'device:control' : `${id}:read`],
    safetyCritical: SAFETY.includes(id),
    ...(SAFETY.includes(id) ? { safetyNote: 'Absence of a warning is not a statement that anything is safe.' } : {}),
    ...(id === 'alarm-feed' || id === 'air-quality' ? { subscribable: true } : {}),
  });
}

export function validateSpec(spec: AgentSpec): void {
  FqdnSchema.parse(spec.fqdn);
  if (spec.capabilities.length === 0) throw new Error('choose at least one capability');
  for (const c of spec.capabilities) if (!CAPABILITY_IDS.includes(c)) throw new Error(`unknown capability "${c}"`);
  for (const c of spec.capabilities) PAYLOAD_SCHEMAS[c].parse(EXAMPLE_PAYLOADS[c]); // the shipped example must be valid
}

export function createAgentFiles(spec: AgentSpec): Record<string, string> {
  validateSpec(spec);
  const card = {
    agent: {
      fqdn: spec.fqdn,
      version: '1.0.0',
      name: spec.name,
      description: `${spec.name}: an accessibility agent published with create-sense-agent.`,
    },
    covers: spec.covers,
    capabilities: spec.capabilities.map(capabilityPreset),
  };
  const providers = spec.capabilities.map((c) => `  '${c}': () => (${JSON.stringify(EXAMPLE_PAYLOADS[c])}),`).join('\n');
  return {
    'sense-card.json': JSON.stringify(card, null, 2) + '\n',
    'agent.ts': `import { readFileSync } from 'node:fs';
import type { Clock } from '@sense/protocol';
import { PublisherIdentity, type SimulatedRegistry } from '@sense/identity';
import { SimAgent } from '@sense/world-sim';

/**
 * ${spec.name} (${spec.fqdn}), a SENSE publisher agent for the SIMULATED registry.
 * EDIT THE PROVIDERS BELOW: return your real data. Every payload is validated against the schema in
 * @sense/protocol by SENSE, so invalid data is rejected rather than shown.
 */
export const providers = {
${providers}
};

export async function createAgent(registry: SimulatedRegistry, clock?: Clock): Promise<SimAgent> {
  const spec = JSON.parse(readFileSync(new URL('./sense-card.json', import.meta.url), 'utf8'));
  const identity = await PublisherIdentity.create({
    registry,
    fqdn: spec.agent.fqdn,
    version: spec.agent.version,
    name: spec.agent.name,
    description: spec.agent.description,
    capabilities: spec.capabilities,
    covers: spec.covers,
    simulated: true,
    ...(clock ? { clock } : {}),
  });
  return new SimAgent(identity, providers as never, clock);
}
`,
    'run.ts': `import { HelloAckSchema, systemClock } from '@sense/protocol';
import { SimulatedAnsClient, SimulatedRegistry, loadOrCreateAuthority, randomB64 } from '@sense/identity';
import { createAgent } from './agent';

/** Registers the agent in an in-memory SIMULATED registry and runs SENSE's 7-step verification against it. */
const t0 = performance.now();
const registry = new SimulatedRegistry(await loadOrCreateAuthority(undefined));
const agent = await createAgent(registry);
const client = new SimulatedAnsClient(registry);
const record = await client.resolve(agent.fqdn);
const hello = { v: 1 as const, id: 'h1', sessionId: 'session-' + randomB64(9).replace(/[^a-z0-9]/gi, 'x'), ts: new Date(systemClock.now()).toISOString(), type: 'hello' as const, ephemeralPublicKey: randomB64(32), nonce: randomB64(24), protocols: ['sense/0.1'] };
const ack = HelloAckSchema.parse(await agent.handle(hello));
const result = await client.verify(record, { fqdn: ack.fqdn, sessionId: hello.sessionId, ephemeralPublicKey: hello.ephemeralPublicKey, nonce: hello.nonce, presentation: ack.presentation });
for (const s of result.steps) console.log(\`  \${s.status === 'pass' ? 'PASS' : s.status.toUpperCase()} step \${s.step}: \${s.name}\`);
console.log(\`\${agent.fqdn}: \${result.outcome} (ANS-modeled, simulated) in \${(performance.now() - t0).toFixed(0)} ms\`);
process.exit(result.outcome === 'VERIFIED' ? 0 : 1);
`,
    'README.md': `# ${spec.name}

A SENSE publisher agent for \`${spec.fqdn}\`, generated by \`create-sense-agent\`.

1. Edit the \`providers\` in \`agent.ts\` to return your real data (menu, map, arrivals, ...).
2. Edit \`sense-card.json\`: names, freshness limits, scopes. The signed Sense Card is built from it at registration.
3. Run \`npx tsx agents/${spec.fqdn}/run.ts\` to register into the simulated registry and run the 7 verification steps.

This registers only in SENSE's **simulated** registry. It does not publish to a real ANS registry.
`,
  };
}

export function writeAgent(spec: AgentSpec, outDir: string): string {
  const dir = join(outDir, spec.fqdn);
  mkdirSync(dir, { recursive: true });
  for (const [name, text] of Object.entries(createAgentFiles(spec))) writeFileSync(join(dir, name), text);
  return dir;
}

// ── CLI ──────────────────────────────────────────────────────────────────────────────────────
if (process.argv[1]?.endsWith('create-sense-agent.ts')) {
  const args = process.argv.slice(2);
  const fqdn = args.find((a) => !a.startsWith('--'));
  const flag = (n: string) => {
    const i = args.indexOf(`--${n}`);
    return i >= 0 ? args[i + 1] : undefined;
  };
  const all = (n: string) => args.flatMap((a, i) => (a === `--${n}` && args[i + 1] ? [args[i + 1] as string] : []));
  if (!fqdn) {
    console.error(
      'usage: npm run create-sense-agent -- <name.sim> [--name "Display name"] [--capability arrivals]... [--covers area]... [--out agents]',
    );
    process.exit(2);
  }
  const spec: AgentSpec = {
    fqdn,
    name: flag('name') ?? fqdn,
    capabilities: (all('capability').length ? all('capability') : ['accessibility-features']) as CapabilityId[],
    covers: all('covers').length ? all('covers') : ['riverside'],
  };
  const out = flag('out') ?? join(dirname(fileURLToPath(import.meta.url)), '..', 'agents');
  const dir = writeAgent(spec, out);
  console.log(`Created ${dir}\nNext: npx tsx ${join(dir, 'run.ts')}`);
}
