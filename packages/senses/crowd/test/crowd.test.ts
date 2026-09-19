import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ManualClock, PERSONA_IDS, PERSONAS, containsAssurance, type Percept, type SenseModule } from '@sense/protocol';
import { route } from '@sense/render';
import { CrowdSense, levelFor, simulatedCrowd } from '../src';

const collect = async (m: SenseModule) => {
  const out: Percept[] = [];
  for await (const p of m.produce()) out.push(p);
  return out;
};

describe('crowd-density plugin', () => {
  it('is a plain SenseModule that needs no core changes: under 100 lines and importing only @sense/protocol', () => {
    const src = readFileSync(join(import.meta.dirname, '../src/index.ts'), 'utf8');
    expect(src.trim().split('\n').length).toBeLessThan(100);
    const imports = [...src.matchAll(/from '([^']+)'/g)].map((m) => m[1]);
    expect(imports).toEqual(['@sense/protocol']);
  });

  it('produces INFERRED, simulated, hedged percepts only when the level changes', async () => {
    const readings = async function* () {
      for (const d of [0.1, 0.15, 0.5, 0.55, 0.9, 0.95, 0.2]) yield { zone: 'Main hall', density: d };
    };
    const ps = await collect(new CrowdSense(readings, new ManualClock()));
    expect(ps.map((p) => p.short)).toEqual([
      'Main hall is quiet. Inferred, simulated sensor.',
      'Main hall is busy. Inferred, simulated sensor.',
      'Main hall is very crowded. Inferred, simulated sensor.',
      'Main hall is quiet. Inferred, simulated sensor.',
    ]);
    expect(ps.every((p) => p.provenance.tier === 'INFERRED' && p.simulated === true)).toBe(true);
    expect(ps.map((p) => p.urgency)).toEqual([0, 1, 2, 0]);
    expect(ps.every((p) => !containsAssurance(p.short) && !containsAssurance(p.long ?? ''))).toBe(true);
  });

  it('works through the existing router for every persona with no special casing', async () => {
    const [p] = await collect(new CrowdSense(() => simulatedCrowd(3, 4)));
    expect(p).toBeDefined();
    for (const id of PERSONA_IDS) {
      const plan = route(p as Percept, PERSONAS[id]);
      expect(plan.modalities.length).toBeGreaterThan(0);
      expect(plan.visual.simulated).toBe(true);
    }
  });

  it('the simulated sensor is deterministic', async () => {
    const a: unknown[] = [];
    const b: unknown[] = [];
    for await (const r of simulatedCrowd(7)) a.push(r);
    for await (const r of simulatedCrowd(7)) b.push(r);
    expect(a).toEqual(b);
    expect(a).toHaveLength(6);
    expect(levelFor(0.99).word).toBe('very crowded');
    expect(levelFor(2).word).toBe('very crowded');
  });
});
