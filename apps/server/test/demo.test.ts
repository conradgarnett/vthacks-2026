import { afterEach, describe, expect, it } from 'vitest';
import { ManualClock } from '@sense/protocol';
import {
  SCENES,
  SenseContext,
  buildServer,
  registerDemoRoutes,
  registerDeviceRoutes,
  registerHearingRoutes,
  registerTasteRoutes,
  registerVisionRoutes,
  runScenes,
  type SceneClient,
} from '../src';

const closers: Array<() => Promise<void>> = [];
afterEach(async () => {
  while (closers.length) await closers.pop()?.();
});

async function start() {
  const clock = new ManualClock();
  const ctx = await SenseContext.create({ env: {}, clock, online: false });
  const app = await buildServer(ctx, {
    extra: [registerVisionRoutes, registerHearingRoutes, registerTasteRoutes, registerDeviceRoutes, registerDemoRoutes],
  });
  closers.push(() => app.close());
  const client: SceneClient = {
    get: async (path) => (await app.inject({ method: 'GET', url: path })).json(),
    post: async (path, body, via = 'demo') => {
      const r = await app.inject({ method: 'POST', url: path, payload: (body ?? {}) as never, headers: { 'x-sense-via': via } });
      return { status: r.statusCode, body: r.json() };
    },
    settle: (ms = 60) => new Promise((r) => setTimeout(r, Math.min(ms, 120))),
  };
  return { ctx, app, clock, client };
}

describe('the golden demo scenes (the same code demo:verify runs)', () => {
  it('all seven scenes pass every assertion, offline, with simulated time skipping', async () => {
    const { ctx, client, clock } = await start();
    const results = await runScenes({ ctx, client, skipTime: (ms) => (clock.advance(ms), true) });
    expect(results.map((r) => r.id)).toEqual(SCENES.map((s) => s.id));
    const failed = results.flatMap((r) => r.checks.filter((c) => !c.ok).map((c) => `scene ${r.id}: ${c.name} (${c.detail ?? ''})`));
    expect(failed).toEqual([]);
    expect(results.reduce((n, r) => n + r.checks.length, 0)).toBeGreaterThan(55);
    for (const r of results) expect(r.checks.length, `scene ${r.id} asserts something`).toBeGreaterThan(5);
  }, 60_000);

  it('a scene that throws is reported as a failed check, not swallowed', async () => {
    const { ctx, client } = await start();
    const boom: SceneClient = {
      ...client,
      get: async () => {
        throw new Error('boom');
      },
    };
    const results = await runScenes({ ctx, client: boom, skipTime: () => false }, {}, [1]);
    expect(results[0]?.checks.at(-1)).toMatchObject({ name: 'scene ran without throwing', ok: false, detail: 'boom' });
  });
});

describe('demo routes', () => {
  it('lists scenes, runs one, rejects unknown scenes, and skips simulated time (labelled)', async () => {
    const { app, ctx, clock } = await start();
    const list = (await app.inject({ method: 'GET', url: '/api/demo/scenes' })).json();
    expect(list.scenes).toHaveLength(SCENES.length);
    expect(list.running).toBe(false);
    expect((await app.inject({ method: 'POST', url: '/api/demo/scene/99' })).statusCode).toBe(404);

    const run = await app.inject({ method: 'POST', url: '/api/demo/scene/1' });
    expect(run.statusCode).toBe(200);
    expect(run.json().results[0].checks.every((c: { ok: boolean }) => c.ok)).toBe(true);
    expect((await app.inject({ method: 'GET', url: '/api/demo/scenes' })).json().last).toHaveLength(1);

    const before = clock.now();
    const skip = await app.inject({ method: 'POST', url: '/api/demo/skip-time', payload: { seconds: 120 } });
    expect(skip.json()).toMatchObject({ ok: true, label: 'SIMULATED TIME SKIP', seconds: 120 });
    expect(clock.now() - before).toBe(120_000);
    expect(ctx.actions.at(-1)).toMatchObject({ kind: 'skip-time', detail: 'SIMULATED TIME SKIP: 120s' });
    expect((await app.inject({ method: 'POST', url: '/api/demo/skip-time', payload: { seconds: 0 } })).statusCode).toBe(400);
  });

  it('starts auto-play in the background and refuses to overlap runs', async () => {
    const { app } = await start();
    const first = await app.inject({ method: 'POST', url: '/api/demo/autoplay' });
    expect(first.json()).toMatchObject({ started: true });
    expect((await app.inject({ method: 'POST', url: '/api/demo/autoplay' })).statusCode).toBe(409);
    expect((await app.inject({ method: 'POST', url: '/api/demo/scene/2' })).statusCode).toBe(409);
    for (let i = 0; i < 80 && (await app.inject({ method: 'GET', url: '/api/demo/scenes' })).json().running; i++)
      await new Promise((r) => setTimeout(r, 250));
    const done = (await app.inject({ method: 'GET', url: '/api/demo/scenes' })).json();
    expect(done.running).toBe(false);
    expect(done.last.flatMap((r: { checks: { ok: boolean }[] }) => r.checks).every((c: { ok: boolean }) => c.ok)).toBe(true);
  }, 60_000);
});
