import { execFileSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { CAPABILITY_IDS, PAYLOAD_SCHEMAS, SenseCardCapabilitySchema } from '@sense/protocol';
import { EXAMPLE_PAYLOADS, capabilityPreset, createAgentFiles, validateSpec, writeAgent } from './create-sense-agent';

const TMP = join(import.meta.dirname, '..', '.sense', 'tmp', 'agents');
afterAll(() => rmSync(join(import.meta.dirname, '..', '.sense', 'tmp'), { recursive: true, force: true }));

describe('create-sense-agent template', () => {
  it('ships a valid example payload and Sense Card preset for every capability', () => {
    for (const id of CAPABILITY_IDS) {
      expect(() => PAYLOAD_SCHEMAS[id].parse(EXAMPLE_PAYLOADS[id]), id).not.toThrow();
      expect(() => SenseCardCapabilitySchema.parse(capabilityPreset(id)), id).not.toThrow();
    }
    expect(capabilityPreset('alarm-feed')).toMatchObject({ safetyCritical: true, subscribable: true, freshness: { maxAgeSeconds: 30 } });
    expect(capabilityPreset('indoor-map').freshness.maxAgeSeconds).toBeNull();
  });

  it('rejects bad names and empty capability lists', () => {
    expect(() => validateSpec({ fqdn: 'Not A Domain', name: 'x', capabilities: ['arrivals'], covers: [] })).toThrow();
    expect(() => validateSpec({ fqdn: 'x.sim', name: 'x', capabilities: [], covers: [] })).toThrow(/at least one/);
    expect(() => validateSpec({ fqdn: 'x.sim', name: 'x', capabilities: ['telepathy' as never], covers: [] })).toThrow(
      /unknown capability/,
    );
  });

  it('generated files are self-consistent', () => {
    const files = createAgentFiles({
      fqdn: 'my-cafe.sim',
      name: 'My Cafe',
      capabilities: ['menu-allergens', 'arrivals'],
      covers: ['riverside'],
    });
    expect(Object.keys(files).sort()).toEqual(['README.md', 'agent.ts', 'run.ts', 'sense-card.json']);
    const card = JSON.parse(files['sense-card.json'] as string);
    expect(card.capabilities.map((c: { id: string }) => c.id)).toEqual(['menu-allergens', 'arrivals']);
    expect(files['agent.ts']).toContain("'menu-allergens'");
    expect(files['README.md']).toMatch(/simulated\*\* registry/);
  });

  it('a generated agent registers, runs, and passes all 7 verification steps (timed)', () => {
    const t0 = performance.now();
    const dir = writeAgent(
      {
        fqdn: 'template-cafe.sim',
        name: 'Template Cafe',
        capabilities: ['menu-allergens', 'accessibility-features'],
        covers: ['riverside'],
      },
      TMP,
    );
    const generated = performance.now() - t0;
    const t1 = performance.now();
    const out = execFileSync(process.execPath, ['--import', 'tsx', join(dir, 'run.ts')], {
      cwd: join(import.meta.dirname, '..'),
      encoding: 'utf8',
    });
    const verified = performance.now() - t1;
    expect(out.match(/PASS step/g)).toHaveLength(7);
    expect(out).toMatch(/template-cafe\.sim: VERIFIED \(ANS-modeled, simulated\)/);
    console.log(
      `[onboarding timing] generate ${generated.toFixed(0)} ms; register + verify in a fresh process ${verified.toFixed(0)} ms (machine time only; human editing time was not measured)`,
    );
  }, 60_000);
});
