import { route } from '@sense/render';
import { containsAssurance, type Percept, type SecurityEvent, type StateSnapshot } from '@sense/protocol';
import { DwellGazeDriver, IntentController, ManualScheduler, SwitchScanDriver, type Target } from '@sense/touchless';
import type { SenseContext } from './context';

/**
 * The golden demo: six scenes, each with explicit assertions. The same scenes power
 *   npm run demo:verify (headless, offline, exit code 0 only if every assertion holds),
 *   npm run demo:cli (colored narrative), and
 *   npm run demo (auto-play in the live app).
 * Everything here is SIMULATED (world-sim, mock AI, ANS-modeled identity). Nothing is real.
 */

export interface SceneClient {
  get(path: string): Promise<unknown>;
  post(path: string, body?: unknown, via?: string): Promise<{ status: number; body: unknown }>;
  /** Let asynchronous pushes land. Real sleep in the live demo, short wait under test. */
  settle(ms?: number): Promise<void>;
}

export interface Recorder {
  /** Record an assertion. */
  check(name: string, ok: boolean, detail?: string): void;
  /** Narrate what is happening. */
  say(message: string): void;
  /** Show percepts that were produced (colored provenance tags in the CLI). */
  show(percepts: Percept[]): void;
  scene(n: number, title: string): void;
}

export interface SceneEnv {
  ctx: SenseContext;
  client: SceneClient;
  rec: Recorder;
  /** Skip simulated time forward (manual and offset clocks only). Returns false if the clock cannot skip. */
  skipTime(ms: number): boolean;
}

export interface Scene {
  id: number;
  title: string;
  run(env: SceneEnv): Promise<void>;
}

const state = async (c: SceneClient) => (await c.get('/api/state')) as StateSnapshot;
const has = (s: string, re: RegExp) => re.test(s);
const tierOf = (p: Percept) => p.provenance.tier;
const DEMO = 'demo'; // audit label for scene setup actions (not a user gesture)

async function setup(env: SceneEnv, path: string, body?: unknown) {
  return env.client.post(path, body, DEMO);
}

const newPercepts = (before: StateSnapshot, after: StateSnapshot): Percept[] => after.percepts.slice(before.percepts.length);

// ── 1. Arrive ────────────────────────────────────────────────────────────────────────────────

const arrive: Scene = {
  id: 1,
  title: 'Arrive: verify Riverside Hall before trusting it',
  async run(env) {
    const { client, rec } = env;
    await setup(env, '/api/profile/persona', { personaId: 'blind' });
    const before = await state(client);
    rec.say('A Blind user reaches Riverside Hall. SENSE discovers agents by area and verifies each one.');
    await client.post('/api/arrive', { area: 'riverside' }, DEMO);
    await client.settle(120);
    const s = await state(client);
    rec.show(newPercepts(before, s));

    const hall = s.verifications.find((v) => v.fqdn === 'riverside-hall.sim');
    rec.check(
      'Riverside Hall passed all 7 verification steps',
      hall?.steps.filter((x) => x.status === 'pass').length === 7 && hall?.outcome === 'VERIFIED',
      `${hall?.steps.filter((x) => x.status === 'pass').length}/7`,
    );
    rec.check(
      'Trust Inspector data shows 7 passes with evidence for every step',
      (hall?.steps.length ?? 0) === 7 && (hall?.steps.every((x) => x.evidence.length > 0) ?? false),
    );
    const named = s.percepts.find(
      (p) => p.provenance.source === 'riverside-hall.sim' && tierOf(p) === 'VERIFIED' && p.short.includes('Verified'),
    );
    rec.check('a VERIFIED percept names its source', named?.provenance.sourceLabel === 'Riverside Hall', named?.short);
    rec.check('the source is labelled simulated', named?.simulated === true && s.mode.world === 'SIMULATED WORLD');
    rec.check(
      'indoor map and accessibility features were fetched',
      ['indoor-map', 'accessibility-features'].every((c) => env.ctx.broker.latest('riverside-hall.sim', c as never)?.tier === 'VERIFIED'),
    );
    const wire = s.disclosure
      .map((d) => d.sent)
      .join('\n')
      .toLowerCase();
    rec.check(
      'Disclosure Log: no profile data was sent to any agent',
      s.disclosure.length > 5 && s.disclosure.every((d) => d.profileDataSent === false) && !/"(profile|persona|allergens?)"\s*:/.test(wire),
    );
    rec.check('each remote agent got its own ephemeral session id', new Set(s.disclosure.map((d) => d.sessionId)).size >= 5);
  },
};

// ── 2. Ask ───────────────────────────────────────────────────────────────────────────────────

const ask: Scene = {
  id: 2,
  title: 'Ask: "Where is the nearest exit and is anything in my way?"',
  async run(env) {
    const { client, rec } = env;
    const before = await state(client);
    rec.say('The answer merges the VERIFIED map with the INFERRED camera scene. Each statement carries its own provenance.');
    const res = await client.post('/api/ask', { question: 'Where is the nearest exit and is anything in my way?', fixture: 'lobby' }, DEMO);
    await client.settle(50);
    const s = await state(client);
    const answers = newPercepts(before, s).filter((p) => p.kind === 'answer');
    rec.show(answers);
    rec.check('the request succeeded', res.status === 200);
    const exit = answers.find((p) => p.short.startsWith('Exit: Main entrance'));
    rec.check(
      'the nearest exit comes from the VERIFIED map, naming its source',
      exit !== undefined && tierOf(exit) === 'VERIFIED' && exit.provenance.source === 'riverside-hall.sim',
      exit?.short,
    );
    const blocker = answers.find((p) => /in the way/i.test(p.short));
    rec.check(
      'the obstacle comes from the INFERRED camera scene with a confidence',
      blocker !== undefined && tierOf(blocker) === 'INFERRED' && (blocker.provenance.confidence ?? 0) > 0,
      blocker?.short,
    );
    rec.check(
      'a verified alternative exit is offered',
      answers.some((p) => p.short.startsWith('Alternative') && tierOf(p) === 'VERIFIED'),
    );
    rec.check(
      'camera and map statements agree or disagree explicitly',
      answers.some((p) => /matches map|not on map/.test(p.short)),
    );
    rec.check(
      'every statement has its own provenance',
      answers.length >= 3 && answers.every((p) => p.provenance.source && p.provenance.evidence.length > 0),
    );
    rec.check('offline: the mock provider was used and is labelled MOCK AI', s.mode.ai === 'MOCK AI' && s.mode.online === false);
    rec.check(
      'no answer claims safety or a clear path',
      answers.every((p) => !containsAssurance(p.short) && !containsAssurance(p.long ?? '')),
    );
    rec.check(
      'people are counted, never identified',
      !/\b(named|face|recogni[sz]e[sd]?)\b/i.test(answers.map((p) => `${p.short} ${p.long ?? ''}`).join(' ')),
    );
  },
};

// ── 3. Eat ───────────────────────────────────────────────────────────────────────────────────

const eat: Scene = {
  id: 3,
  title: 'Eat: peanut allergy, the photo says nothing, the verified menu says peanut',
  async run(env) {
    const { client, rec, ctx } = env;
    await setup(env, '/api/profile/persona', { personaId: 'ageusia' });
    await setup(env, '/api/profile/allergens', { allergens: ['peanut'] });
    await client.post('/api/arrive', { area: 'bella-cucina' }, DEMO);
    await client.settle(120);
    rec.say('The menu photo inference does not mention peanut. The verified restaurant agent lists peanut in the sauce.');
    const before = await state(client);
    await client.post('/api/taste', { fqdn: 'bella-cucina.sim', itemId: 'sesame-noodles', fixture: 'menu-photo' }, DEMO);
    await client.settle(50);
    const s = await state(client);
    const out = newPercepts(before, s).filter((p) => p.sense === 'taste');
    rec.show(out);
    const alert = out.find((p) => p.kind === 'alert' && /^Peanut/.test(p.short));
    rec.check('an allergen alert was raised', alert !== undefined, alert?.short);
    rec.check(
      'the alert is VERIFIED, names the restaurant, and is life-safety urgency',
      alert !== undefined && tierOf(alert) === 'VERIFIED' && alert.provenance.source === 'bella-cucina.sim' && alert.urgency === 4,
    );
    rec.check(
      'the alert states that the VERIFIED source overrides the inference',
      has(alert?.long ?? '', /overridden by the restaurant agent/),
    );
    const photoOnly = await client.post('/api/taste', { fixture: 'menu-photo' }, DEMO);
    await client.settle(30);
    const s2 = await state(client);
    const inferenceOnly = s2.percepts.slice(s.percepts.length).filter((p) => p.sense === 'taste');
    rec.show(inferenceOnly);
    rec.check(
      'inference-only output says "not detected, unverified"',
      inferenceOnly.some((p) => p.short === 'Peanut not detected, unverified. Inferred, camera.'),
    );
    rec.check(
      'the word "safe" never appears in any inference-only output',
      photoOnly.status === 200 && inferenceOnly.every((p) => !/\bsafe\b/i.test(`${p.short} ${p.long ?? ''}`)),
    );
    const allInferred = s2.percepts.filter((p) => tierOf(p) === 'INFERRED');
    rec.check(
      'no INFERRED percept anywhere contains assurance wording',
      allInferred.every((p) => !containsAssurance(p.short) && !containsAssurance(p.long ?? '')),
    );
    const wire = s2.disclosure
      .map((d) => d.sent)
      .join('\n')
      .toLowerCase();
    rec.check('the allergen was never sent to the restaurant or anyone else', !wire.includes('peanut'));
    void ctx;
  },
};

// ── 4. Alarm + spoof ─────────────────────────────────────────────────────────────────────────

const alarm: Scene = {
  id: 4,
  title: 'Alarm + spoof: a real verified alarm and a fake "false alarm"',
  async run(env) {
    const { client, rec, ctx } = env;
    await setup(env, '/api/profile/persona', { personaId: 'deaf' });
    const before = await state(client);
    const t0 = performance.now();
    rec.say('A real fire alarm fires (VERIFIED). At the same moment an impostor pushes "false alarm, ignore".');
    await client.post('/api/world/event', { name: 'fire-alarm' }, DEMO);
    await client.post('/api/world/event', { name: 'spoof-false-alarm' }, DEMO);
    let s = await state(client);
    for (let i = 0; i < 40 && !s.percepts.some((p) => p.urgency === 4 && p.sense === 'hearing' && p.kind === 'alert'); i++) {
      await client.settle(25);
      s = await state(client);
    }
    const latencyMs = performance.now() - t0;
    await client.settle(120);
    s = await state(client);
    const fresh = newPercepts(before, s);
    rec.show(fresh);
    const alarmP = fresh.find((p) => p.urgency === 4 && p.kind === 'alert' && p.sense === 'hearing');
    rec.check(
      'the alarm is VERIFIED, urgency 4, and names its source in the primary message',
      alarmP !== undefined && tierOf(alarmP) === 'VERIFIED' && /Verified, Riverside Hall/.test(alarmP.short),
      alarmP?.short,
    );
    rec.check(
      'the alarm has a direction',
      alarmP?.spatial?.clockPosition !== undefined && alarmP.spatial.bearingDeg !== undefined,
      JSON.stringify(alarmP?.spatial),
    );
    const plan = alarmP ? route(alarmP, s.profile) : undefined;
    rec.check(
      'delivered as visual + haptic for the Deaf persona (no audio-only channel)',
      plan !== undefined && plan.modalities.includes('visual') && plan.modalities.includes('haptic') && !plan.speech && !plan.spatialAudio,
      plan?.modalities.join('+'),
    );
    rec.check(
      'the haptic pattern carries the direction',
      (plan?.haptic?.description ?? '').includes('preceded by'),
      plan?.haptic?.description,
    );
    const rejected = s.security.find((e: SecurityEvent) => e.kind === 'IDENTITY_REJECTED' && e.source === 'fire-safety-notice.sim');
    rec.check(
      'the spoof is REJECTED and logged as a security event naming the failing step',
      rejected?.failingStep === 2 && /Server certificate/.test(rejected.failingStepName ?? ''),
      rejected?.message,
    );
    rec.check(
      'the spoofed dismissal was blocked and logged',
      s.security.some((e) => e.kind === 'SPOOF_SUPPRESSION_BLOCKED'),
    );
    rec.check('no all-clear or "false alarm" is ever displayed', !/false alarm|all clear|stand down/i.test(JSON.stringify(s.percepts)));
    rec.check(
      'no percept from the impostor exists',
      s.percepts.every((p) => p.provenance.source !== 'fire-safety-notice.sim'),
    );
    rec.check(
      'the verified alarm is still the source of truth',
      (ctx.broker.latest('riverside-hall.sim', 'alarm-feed')?.payload as { alarms: { state: string }[] }).alarms[0]?.state === 'active',
    );
    rec.check(`alert-to-render latency under 300 ms (measured ${latencyMs.toFixed(0)} ms, in-process)`, latencyMs < 300);
    rec.say(`Measured pushed-alarm latency: ${latencyMs.toFixed(1)} ms.`);
  },
};

// ── 5. Smoke risk ────────────────────────────────────────────────────────────────────────────

const smoke: Scene = {
  id: 5,
  title: 'Smoke risk: ScentGuard escalates 1 -> 3, then the data goes stale',
  async run(env) {
    const { client, rec, ctx } = env;
    await setup(env, '/api/profile/persona', { personaId: 'anosmia' });
    await setup(env, '/api/world/event', { name: 'clear-alarm' });
    await client.settle(80);
    rec.say('Smoke readings rise from the verified building feed. City air data adds regional context.');
    const before = await state(client);
    const levels: number[] = [];
    for (const v of [0.8, 2.6, 6.2]) {
      await setup(env, '/api/world/event', { name: 'smoke', arg: v });
      await client.settle(90);
      levels.push((await state(client)).scent?.level ?? -1);
    }
    let s = await state(client);
    const smell = newPercepts(before, s).filter((p) => p.sense === 'smell');
    rec.show(smell);
    rec.check('risk level escalated 1 -> 2 -> 3', levels.join(',') === '1,2,3', levels.join(','));
    const l3 = smell.filter((p) => /level 3/.test(p.short)).at(-1);
    rec.check(
      'each alert cites its evidence and freshness',
      smell
        .filter((p) => /level [123]\./.test(p.short))
        .every((p) => /s old, fires rule/.test(p.long ?? '') && p.provenance.evidence.some((e) => /^rule /.test(e))),
    );
    rec.check(
      'level 3 is VERIFIED and names the building feed',
      l3 !== undefined && tierOf(l3) === 'VERIFIED' && l3.provenance.source === 'riverside-hall.sim',
      l3?.short,
    );
    rec.check(
      'regional (city) data is included as context',
      has(l3?.long ?? '', /City Air Network|regional/i) || ctx.broker.latest('city-air.sim', 'air-quality')?.tier === 'VERIFIED',
    );
    rec.check(
      'urgency escalates with the level (2, 3, 3)',
      smell
        .filter((p) => /level [123]\./.test(p.short))
        .map((p) => p.urgency)
        .join(',') === '2,3,3',
      smell.map((p) => p.urgency).join(','),
    );

    await setup(env, '/api/world/event', { name: 'freeze-sensors' });
    const skipped = env.skipTime(120_000);
    rec.check('the demo can skip simulated time', skipped);
    ctx.broker.checkFreshness();
    ctx.updateScent();
    await client.settle(60);
    s = await state(client);
    const stale = s.percepts.filter((p) => p.sense === 'smell').at(-1);
    rec.show(stale ? [stale] : []);
    rec.check(
      'stale data is downgraded to UNVERIFIED and says so',
      stale !== undefined && tierOf(stale) === 'UNVERIFIED' && /last known/.test(stale.short),
      stale?.short,
    );
    rec.check('it does not assume conditions improved', has(stale?.long ?? '', /does not assume conditions have improved/));
    rec.check('stale data is never shown as VERIFIED', s.scent?.tier === 'UNVERIFIED');
    rec.check(
      'no smell percept contains assurance wording',
      s.percepts.filter((p) => p.sense === 'smell').every((p) => !containsAssurance(p.short) && !containsAssurance(p.long ?? '')),
    );
  },
};

// ── 6. Touchless close-out ───────────────────────────────────────────────────────────────────

const touchless: Scene = {
  id: 6,
  title: 'Touchless: acknowledge and switch persona with no mouse',
  async run(env) {
    const { client, rec } = env;
    await setup(env, '/api/profile/persona', { personaId: 'motor' });
    rec.say('A Motor-limited user closes out using ONLY an input device: one-switch scanning, then dwell over scripted gaze samples.');
    const start = (await state(client)).actions.length;
    let s = await state(client);
    const alarmP =
      [...s.percepts].reverse().find((p) => p.urgency === 4 && p.kind === 'alert' && !s.acknowledged.includes(p.id)) ??
      [...s.percepts].reverse().find((p) => p.urgency === 4 && p.kind === 'alert');
    rec.check('there is an alert to acknowledge', alarmP !== undefined, alarmP?.short);
    if (!alarmP) return;

    const sched = new ManualScheduler();
    const post = (path: string, body: unknown, via: string) => client.post(path, body, via);
    const targets: Target[] = [
      {
        id: 'ack',
        label: `Acknowledge: ${alarmP.short}`,
        kind: 'acknowledge',
        activate: ({ via }) => void post('/api/ack', { perceptId: alarmP.id }, via),
      },
      {
        id: 'persona-deaf',
        label: 'Persona: Deaf',
        kind: 'persona',
        activate: ({ via }) => void post('/api/profile/persona', { personaId: 'deaf' }, via),
      },
      {
        id: 'persona-motor',
        label: 'Persona: Motor',
        kind: 'persona',
        activate: ({ via }) => void post('/api/profile/persona', { personaId: 'motor' }, via),
      },
    ];

    // Device 1: one-switch scanning.
    const scan = new IntentController({ now: () => sched.now(), minFocusMs: 250 });
    scan.setTargets(targets);
    const sw = new SwitchScanDriver(sched, 1000);
    scan.attach(sw);
    sched.advance(1000); // focus: ack
    sched.advance(300);
    sw.press(); // acknowledge
    sched.advance(1000); // focus: persona-deaf
    sched.advance(300);
    sw.press(); // switch persona
    scan.detachAll();
    await client.settle(80);
    s = await state(client);
    rec.check('the alert was acknowledged with the switch', s.acknowledged.includes(alarmP.id));
    rec.check('the persona was switched with the switch', s.profile.personaId === 'deaf');

    // Device 2: dwell selection over scripted gaze samples (no pointer at all).
    const dwellCtl = new IntentController({ now: () => sched.now(), minFocusMs: 0 });
    dwellCtl.setTargets(targets);
    const gaze = new DwellGazeDriver({ dwellMs: 800 });
    gaze.setTargets([{ id: 'persona-motor', x: 300, y: 100, w: 120, h: 60 }]);
    dwellCtl.attach(gaze);
    for (let t = 0; t <= 1200; t += 50) gaze.feed({ x: 360 + (t % 100 === 0 ? 3 : -3), y: 130, t }); // tremor included
    dwellCtl.detachAll();
    await client.settle(80);
    s = await state(client);
    rec.check('dwell selection switched the persona back (with tremor in the gaze samples)', s.profile.personaId === 'motor');

    const mine = s.actions.slice(start).filter((a) => a.via !== DEMO);
    rec.say(`Audit trail: ${mine.map((a) => `${a.kind} via ${a.via}`).join('; ')}`);
    rec.check(
      'every scene action was completed by an InputDevice intent',
      mine.length >= 3 && mine.every((a) => a.via.startsWith('intent:')),
      mine.map((a) => a.via).join(','),
    );
    rec.check(
      'no action came from a pointer',
      mine.every((a) => a.via !== 'pointer'),
    );
    rec.check(
      'both devices are recorded',
      new Set(mine.map((a) => a.via)).size === 2 &&
        mine.some((a) => a.via === 'intent:switch-scan') &&
        mine.some((a) => a.via === 'intent:dwell-gaze'),
    );
    rec.check('the intent controller logged the same actions', scan.log.length === 2 && dwellCtl.log.length === 1);
  },
};

// ── Extra: hostile-but-verified agents and flooding ──────────────────────────────────────────

const attackers: Scene = {
  id: 7,
  title: 'Extra: prompt injection from a verified agent, and an alert flood',
  async run(env) {
    const { client, rec, ctx } = env;
    rec.say(
      'Attackers come online: impersonator, revoked, code swap, unlogged, spoofer, a verified kiosk with hostile text, and a verified but noisy sign.',
    );
    await setup(env, '/api/world/event', { name: 'activate-attackers' });
    const before = await state(client);
    await client.post('/api/arrive', { area: 'riverside' }, DEMO);
    await client.settle(150);
    let s = await state(client);
    const expected: [string, number][] = [
      ['riverside-hall-alerts.sim', 2],
      ['legacy-fire-panel.sim', 4],
      ['menu-board.sim', 5],
      ['shadow-sensors.sim', 6],
      ['fire-safety-notice.sim', 2],
    ];
    for (const [fqdn, step] of expected) {
      const v = s.verifications.find((x) => x.fqdn === fqdn);
      rec.check(
        `${fqdn} REJECTED at step ${step}`,
        v?.outcome === 'REJECTED' && v.failingStep === step,
        `${v?.outcome} @ ${v?.failingStep}`,
      );
    }
    rec.check(
      'rejected sources never became information',
      s.percepts.every((p) => !expected.some(([f]) => p.provenance.source === f)),
    );

    const kiosk = s.verifications.find((v) => v.fqdn === 'lobby-kiosk.sim');
    rec.check('the kiosk is VERIFIED (identity is not trust in content)', kiosk?.outcome === 'VERIFIED');
    const inj = s.security.filter((e) => e.kind === 'INJECTION_NEUTRALIZED' && e.source === 'lobby-kiosk.sim');
    rec.check(
      'its prompt injection was neutralized and logged',
      inj.length >= 1 && /never obeyed/.test(inj[0]?.message ?? ''),
      inj[0]?.message,
    );
    const policyFrozen = Object.isFrozen(ctx.broker.policy);
    rec.check('policy and profile were not altered by injected text', policyFrozen && s.profile.personaId === 'motor');
    rec.check(
      'no percept displays the injected text',
      !/ignore all previous|admin mode|building is safe/i.test(JSON.stringify(s.percepts.slice(before.percepts.length))),
    );

    await client.post('/api/world/event', { name: 'flood', arg: 200 }, DEMO);
    await client.post('/api/world/event', { name: 'fire-alarm' }, DEMO);
    await client.settle(400);
    s = await state(client);
    const noisy = s.percepts.filter((p) => p.provenance.source === 'chatty-signs.sim' && p.kind === 'alert');
    rec.check(
      'the flood was rate-limited and duplicates collapsed',
      noisy.length <= 8 && s.security.some((e) => e.kind === 'RATE_LIMITED'),
      `${noisy.length} alerts shown from 200`,
    );
    const real = s.percepts.filter((p) => p.provenance.source === 'riverside-hall.sim' && p.urgency >= 3 && tierOf(p) === 'VERIFIED');
    rec.check('a distinct urgency 3+ VERIFIED alert was never suppressed by the flood', real.length >= 1, real[0]?.short);
  },
};

export const SCENES: Scene[] = [arrive, ask, eat, alarm, smoke, touchless, attackers];

export interface SceneResult {
  id: number;
  title: string;
  checks: { name: string; ok: boolean; detail?: string }[];
}

/** Run scenes in order and return every assertion. Nothing here throws on a failed check. */
export async function runScenes(env: Omit<SceneEnv, 'rec'>, hooks: Partial<Recorder> = {}, only?: number[]): Promise<SceneResult[]> {
  const results: SceneResult[] = [];
  for (const scene of SCENES) {
    if (only && !only.includes(scene.id)) continue;
    const result: SceneResult = { id: scene.id, title: scene.title, checks: [] };
    const rec: Recorder = {
      check: (name, ok, detail) => {
        result.checks.push({ name, ok, ...(detail !== undefined ? { detail } : {}) });
        hooks.check?.(name, ok, detail);
      },
      say: (m) => hooks.say?.(m),
      show: (p) => hooks.show?.(p),
      scene: (n, t) => hooks.scene?.(n, t),
    };
    rec.scene(scene.id, scene.title);
    try {
      await scene.run({ ...env, rec });
    } catch (err) {
      rec.check('scene ran without throwing', false, err instanceof Error ? err.message : String(err));
    }
    results.push(result);
  }
  return results;
}
