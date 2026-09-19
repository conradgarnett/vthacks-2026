import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const read = (p: string) => readFileSync(join(root, p), 'utf8');
const pkg = JSON.parse(read('package.json')) as { scripts: Record<string, string>; workspaces: string[] };
const docs = [
  'README.md',
  ...readdirSync(join(root, 'docs'))
    .filter((f) => f.endsWith('.md'))
    .map((f) => `docs/${f}`),
];

describe('documentation stays consistent with the code', () => {
  it('every `npm run <script>` mentioned in the docs exists in package.json', () => {
    const missing: string[] = [];
    for (const d of docs) {
      for (const m of read(d).matchAll(/npm run ([a-z0-9:-]+)/g)) if (!pkg.scripts[m[1] as string]) missing.push(`${d}: ${m[1]}`);
    }
    expect(missing).toEqual([]);
  });

  it('every relative markdown link resolves to a file', () => {
    const broken: string[] = [];
    for (const d of docs) {
      for (const m of read(d).matchAll(/\]\(([^)#\s]+)(?:#[^)]*)?\)/g)) {
        const target = m[1] as string;
        if (/^[a-z]+:/i.test(target)) continue;
        if (!existsSync(resolve(root, dirname(d), target))) broken.push(`${d}: ${target}`);
      }
    }
    expect(broken).toEqual([]);
  });

  it('every required document exists and is non-trivial', () => {
    for (const f of [
      'README.md',
      'docs/ARCHITECTURE.md',
      'docs/THREAT_MODEL.md',
      'docs/SENSE_CARD_SPEC.md',
      'docs/DEMO_SCRIPT.md',
      'docs/PITCH.md',
      'docs/ROADMAP.md',
      'docs/ANS_NOTES.md',
    ]) {
      expect(read(f).length, f).toBeGreaterThan(1500);
    }
  });

  it('the README carries the limitations notice and the honest mode labels', () => {
    const readme = read('README.md');
    expect(readme).toMatch(/not a medical device and not a certified safety system/);
    expect(readme).toMatch(/SIMULATED WORLD/);
    expect(readme).toMatch(/ANS-modeled/);
    expect(readme).toMatch(/WCAG 2\.2 AA target/);
    expect(readme).toMatch(/not exercised against the live API/);
    expect(readme).toMatch(/not\*\* ANS-compliant/); // states plainly that it is not ANS-compliant
  });

  it('the threat model covers all eight attacks and the DNS/CA dependence', () => {
    const tm = read('docs/THREAT_MODEL.md');
    for (const a of [
      'Impersonator',
      'Revoked agent',
      'Silent code swap',
      'Unlogged agent',
      'Spoofed all-clear',
      'Prompt injection',
      'Stale data',
      'Flooding',
    ])
      expect(tm).toContain(a);
    expect(tm).toMatch(/DNSSEC/);
    expect(tm).toMatch(/certificate/i);
  });

  it('every environment variable the code reads is documented in .env.example', () => {
    const example = read('.env.example');
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(join(root, dir), { withFileTypes: true })) {
        const rel = `${dir}/${e.name}`;
        if (e.isDirectory() && !['node_modules', 'dist', 'test', 'agents'].includes(e.name)) walk(rel);
        else if (/\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) files.push(rel);
      }
    };
    for (const d of ['packages', 'apps', 'scripts']) walk(d);
    const used = new Set<string>();
    for (const f of files) for (const m of read(f).matchAll(/(?:process\.env|\benv)\.([A-Z][A-Z0-9_]{2,})/g)) used.add(m[1] as string);
    const allowed = new Set(['NO_COLOR', 'FORCE_COLOR']); // terminal conventions, not SENSE settings
    const undocumented = [...used].filter((v) => !allowed.has(v) && !new RegExp(`^${v}=`, 'm').test(example));
    expect([...used].length).toBeGreaterThan(8);
    expect(undocumented).toEqual([]);
    expect(example).not.toMatch(/SENSE_LIVE_AIR/); // documented settings must exist in code
    const documented = [...example.matchAll(/^([A-Z][A-Z0-9_]+)=/gm)].map((m) => m[1] as string);
    expect(documented.filter((v) => !used.has(v))).toEqual([]);
  });

  it('every workspace package listed in the README exists', () => {
    for (const dir of [
      'packages/protocol',
      'packages/identity',
      'packages/core',
      'packages/providers',
      'packages/render',
      'packages/senses/crowd',
      'apps/world-sim',
      'apps/server',
      'apps/web',
      'scripts',
      'docs',
    ]) {
      expect(existsSync(join(root, dir)), dir).toBe(true);
    }
    expect(pkg.workspaces).toEqual(['packages/*', 'packages/senses/*', 'apps/*']);
  });

  it('the schemas exported to docs/schemas match the code (run `npm run schemas` if this fails)', async () => {
    const { jsonSchemas } = await import('@sense/protocol');
    for (const [name, schema] of Object.entries(jsonSchemas())) {
      const onDisk = JSON.parse(read(`docs/schemas/${name}.schema.json`));
      expect(onDisk, name).toEqual(JSON.parse(JSON.stringify(schema)));
    }
  });
});
