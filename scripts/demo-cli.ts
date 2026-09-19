import { route } from '@sense/render';
import type { Percept } from '@sense/protocol';
import { runScenes } from '@sense/server';
import { TIER_TAG, color, createHeadlessEnv } from './lib';

/** Text-only walk through the golden demo with colored provenance tags. */
const env = await createHeadlessEnv();
let shown = 0;
let secShown = 0;
let failed = 0;

const tag = (p: Percept) => {
  const t = p.provenance.tier;
  const conf = p.provenance.confidence !== undefined ? ` ${Math.round(p.provenance.confidence * 100)}%` : '';
  return (TIER_TAG[t] ?? color.dim)(`[${t}${conf}]`);
};

function printSecurity() {
  const all = env.ctx.broker.securityEvents();
  for (const e of all.slice(secShown)) {
    const step = e.failingStep ? ` step ${e.failingStep}` : '';
    console.log(`   ${color.red('[SECURITY]')} ${e.kind}${step} ${color.dim(e.source)}: ${e.message}`);
  }
  secShown = all.length;
}

console.log(color.bold('SENSE demo (text mode)') + color.dim('  SIMULATED WORLD | MOCK AI | ANS-modeled identity | offline'));
console.log(
  color.dim('Tags: ') +
    TIER_TAG.VERIFIED!('[VERIFIED]') +
    ' ' +
    TIER_TAG.INFERRED!('[INFERRED 70%]') +
    ' ' +
    TIER_TAG.UNVERIFIED!('[UNVERIFIED]') +
    ' ' +
    TIER_TAG.REJECTED!('[REJECTED]') +
    color.dim(' (rejected sources appear only as security events)'),
);

await runScenes(env, {
  scene: (n, title) => {
    printSecurity();
    console.log(`\n${color.bold(`── Scene ${n}: ${title}`)}`);
  },
  say: (m) => console.log(color.dim(`   ${m}`)),
  show: (ps) => {
    const profile = env.ctx.profiles.current();
    for (const p of ps) {
      const plan = route(p, profile);
      const via = plan.modalities.map((m) => m).join('+');
      console.log(
        `   ${tag(p)} ${p.short} ${color.dim(`(U${p.urgency}, ${p.provenance.sourceLabel ?? p.provenance.source}${p.simulated ? ', simulated' : ''}; ${profile.personaId ?? 'custom'} gets ${via})`)}`,
      );
      shown++;
    }
  },
  check: (name, ok, detail) => {
    if (!ok) failed++;
    console.log(`   ${ok ? color.green('✔') : color.red('✖')} ${name}${!ok && detail ? color.dim(`  (${detail})`) : ''}`);
  },
});
printSecurity();

console.log(
  `\n${failed === 0 ? color.green('All checks passed') : color.red(`${failed} check(s) failed`)}. ${shown} percepts shown, ${env.ctx.broker.securityEvents().length} security events, ${env.ctx.snapshot().disclosure.length} messages disclosed (profile data sent: none).`,
);
await env.close();
process.exit(failed === 0 ? 0 : 1);
