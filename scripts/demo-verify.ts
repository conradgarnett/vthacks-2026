import { SCENES, runScenes } from '@sense/server';
import { color, createHeadlessEnv } from './lib';

/**
 * Headless end-to-end run of the whole golden scenario with assertions. Exit code 0 only if every
 * expected outcome occurs. Runs OFFLINE: any network call fails the run.
 */
let networkCalls = 0;
const realFetch = globalThis.fetch;
globalThis.fetch = (async (...args: Parameters<typeof fetch>) => {
  networkCalls++;
  throw new Error(`demo:verify is offline; blocked fetch to ${String(args[0])}`);
}) as typeof fetch;

const started = performance.now();
const env = await createHeadlessEnv();
let passed = 0;
let failed = 0;

console.log(color.bold('SENSE demo:verify') + color.dim('  (simulated world, mock AI, ANS-modeled identity, offline)'));
const results = await runScenes(env, {
  scene: (n, title) => console.log(`\n${color.bold(`Scene ${n}`)} ${title}`),
  check: (name, ok, detail) => {
    if (ok) passed++;
    else failed++;
    console.log(`  ${ok ? color.green('PASS') : color.red('FAIL')} ${name}${!ok && detail ? color.dim(`  (${detail})`) : ''}`);
  },
});

// Global invariants across the whole run.
const s = env.ctx.snapshot();
const global: [string, boolean][] = [
  ['no network calls were made (offline)', networkCalls === 0],
  ['every scene ran and asserted something', results.length === SCENES.length && results.every((r) => r.checks.length > 0)],
  ['no REJECTED-tier percept was ever routed', s.percepts.every((p) => p.provenance.tier !== 'REJECTED')],
  [
    'no percept anywhere contains "safe" or "all clear" wording',
    s.percepts.every((p) => !/\b(safe|safely|all[- ]clear)\b/i.test(`${p.short} ${p.long ?? ''}`)),
  ],
  ['every disclosure entry says no profile data was sent', s.disclosure.every((d) => d.profileDataSent === false)],
  [
    'the simulated world is labelled',
    s.mode.world === 'SIMULATED WORLD' && s.mode.ai === 'MOCK AI' && s.mode.ans === 'ANS-modeled (simulated)',
  ],
];
console.log(`\n${color.bold('Global checks')}`);
for (const [name, ok] of global) {
  if (ok) passed++;
  else failed++;
  console.log(`  ${ok ? color.green('PASS') : color.red('FAIL')} ${name}`);
}

await env.close();
globalThis.fetch = realFetch;
const secs = ((performance.now() - started) / 1000).toFixed(1);
console.log(
  `\n${failed === 0 ? color.green('ALL PASSED') : color.red('FAILED')}: ${passed} passed, ${failed} failed in ${secs}s across ${results.length} scenes`,
);
console.log(color.dim('Everything above is simulated. SENSE is a prototype, not a medical device or a certified safety system.'));
process.exit(failed === 0 ? 0 : 1);
