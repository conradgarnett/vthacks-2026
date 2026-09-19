import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it } from 'vitest';
import WebSocket from 'ws';
import { ManualClock, type ServerEvent, type StateSnapshot } from '@sense/protocol';
import { SenseContext, attachWebSocket, buildServer } from '../src';

const cleanups: Array<() => Promise<void> | void> = [];
afterEach(async () => {
  while (cleanups.length) await cleanups.pop()?.();
});

async function start(opts: { manual?: boolean; webDist?: string } = {}) {
  const ctx = await SenseContext.create({ env: {}, ...(opts.manual === false ? {} : { clock: new ManualClock() }), online: false });
  const app = await buildServer(ctx, opts.webDist ? { webDist: opts.webDist } : {});
  cleanups.push(() => app.close());
  const get = async (url: string) => (await app.inject({ method: 'GET', url })).json();
  const post = async (url: string, payload?: unknown, headers: Record<string, string> = {}) =>
    app.inject({ method: 'POST', url, payload: payload as never, headers });
  const state = async () => (await get('/api/state')) as StateSnapshot;
  const settle = () => new Promise((r) => setTimeout(r, 60));
  return { ctx, app, get, post, state, settle };
}

describe('state and honesty labels', () => {
  it('reports simulated world, ANS-modeled identity and mock AI when offline and keyless', async () => {
    const { state } = await start();
    const s = await state();
    expect(s.mode).toEqual({ world: 'SIMULATED WORLD', ans: 'ANS-modeled (simulated)', ai: 'MOCK AI', online: false });
    expect(s.profile.personaId).toBe('blind');
    expect(s.percepts).toEqual([]);
    expect(s.user.place).toBe('riverside-hall');
  });

  it('labels live AI only when a key is present', async () => {
    const ctx = await SenseContext.create({ env: { ANTHROPIC_API_KEY: 'x' }, clock: new ManualClock() });
    expect(ctx.mode.ai).toBe('ANTHROPIC');
    const mock = await SenseContext.create({ env: { ANTHROPIC_API_KEY: 'x', SENSE_PROVIDER: 'mock' }, clock: new ManualClock() });
    expect(mock.mode.ai).toBe('MOCK AI');
    const live = await SenseContext.create({ env: { ANS_MODE: 'live' }, clock: new ManualClock() });
    expect(live.mode.ans).toBe('live ANS (not configured)');
  });
});

describe('arrive and verification', () => {
  it('discovers, verifies and exposes 7 steps per agent for the trust inspector', async () => {
    const { post, state, settle, get } = await start();
    const res = await post('/api/arrive', { area: 'riverside' });
    expect(res.statusCode).toBe(200);
    await settle();
    const s = await state();
    const hall = s.verifications.find((v) => v.fqdn === 'riverside-hall.sim');
    expect(hall?.outcome).toBe('VERIFIED');
    expect(hall?.steps.filter((x) => x.status === 'pass')).toHaveLength(7);
    expect(hall?.simulated).toBe(true);
    expect(s.percepts.some((p) => p.provenance.source === 'riverside-hall.sim' && p.provenance.tier === 'VERIFIED')).toBe(true);
    expect(s.disclosure.every((d) => d.profileDataSent === false)).toBe(true);
    expect(s.disclosure.length).toBeGreaterThan(5);
    expect((await get('/api/verifications/riverside-hall.sim')).outcome).toBe('VERIFIED');
  });

  it('404 for an agent we never verified; 400 for a malformed body', async () => {
    const { app, post } = await start();
    expect((await app.inject({ method: 'GET', url: '/api/verifications/nope.sim' })).statusCode).toBe(404);
    expect((await post('/api/arrive', {})).statusCode).toBe(400);
    expect((await post('/api/arrive', { area: 'x', extra: 1 })).statusCode).toBe(400);
  });
});

describe('profile', () => {
  it('switches personas, keeps declared allergens, and records how it was done', async () => {
    const { post, state } = await start();
    await post('/api/profile/allergens', { allergens: ['Peanut', ' peanut ', 'sesame'] });
    expect((await state()).profile.allergens).toEqual(['peanut', 'sesame']);
    const res = await post('/api/profile/persona', { personaId: 'deaf' }, { 'x-sense-via': 'intent:scripted' });
    expect(res.statusCode).toBe(200);
    const s = await state();
    expect(s.profile.personaId).toBe('deaf');
    expect(s.profile.allergens).toEqual(['peanut', 'sesame']);
    expect(s.actions.at(-1)).toMatchObject({ kind: 'persona', via: 'intent:scripted', detail: 'deaf' });
    expect((await post('/api/profile/persona', { personaId: 'wizard' })).statusCode).toBe(400);
  });

  it('sanitizes a hostile x-sense-via header', async () => {
    const { post, state } = await start();
    await post('/api/profile/persona', { personaId: 'motor' }, { 'x-sense-via': 'intent:<script>' });
    expect((await state()).actions.at(-1)?.via).toBe('pointer');
  });

  it('situational presets layer over the persona and can be cleared', async () => {
    const { post, state } = await start();
    await post('/api/profile/situation', { preset: 'noisy-room' });
    expect((await state()).profile.output['4']).not.toContain('speech');
    expect((await state()).profile.situational?.preset).toBe('noisy-room');
    await post('/api/profile/situation', { preset: null });
    expect((await state()).profile.output['4']).toContain('speech');
  });

  it('accepts a custom profile and rejects an invalid one', async () => {
    const { post, state } = await start();
    const base = (await state()).profile;
    expect((await post('/api/profile/custom', { profile: { ...base, name: 'Mine', speechRate: 2 } })).statusCode).toBe(200);
    expect((await state()).profile.speechRate).toBe(2);
    expect((await post('/api/profile/custom', { profile: { ...base, speechRate: 99 } })).statusCode).toBe(400);
  });

  it('exports a signed profile and imports only files signed by this device’s key', async () => {
    const { app, post, state } = await start();
    await post('/api/profile/persona', { personaId: 'anosmia' });
    const file = (await app.inject({ method: 'GET', url: '/api/profile/export' })).json();
    expect(file.format).toBe('sense-profile/0.1');
    await post('/api/profile/persona', { personaId: 'blind' });
    expect((await post('/api/profile/import', file)).statusCode).toBe(200);
    expect((await state()).profile.personaId).toBe('anosmia');
    const tampered = { ...file, profile: { ...file.profile, speechRate: 3 } };
    expect((await post('/api/profile/import', tampered)).statusCode).toBe(400);
    expect((await post('/api/profile/import', { nope: true })).statusCode).toBe(400);
  });
});

describe('alarm, acknowledge and the spoof scene through the API', () => {
  it('delivers a verified alarm, rejects a spoof with the failing step, and records the acknowledgement channel', async () => {
    const { post, state, settle } = await start();
    await post('/api/profile/persona', { personaId: 'deaf' });
    await post('/api/arrive', { area: 'riverside' });
    await settle();
    expect((await post('/api/world/event', { name: 'fire-alarm' })).statusCode).toBe(200);
    await settle();
    await post('/api/world/event', { name: 'spoof-false-alarm' });
    await settle();
    const s = await state();
    const alarm = s.percepts.find((p) => p.urgency === 4);
    expect(alarm?.provenance).toMatchObject({ tier: 'VERIFIED', source: 'riverside-hall.sim' });
    const rejected = s.security.find((e) => e.kind === 'IDENTITY_REJECTED' && e.source === 'fire-safety-notice.sim');
    expect(rejected?.failingStep).toBe(2);
    expect(JSON.stringify(s.percepts)).not.toMatch(/false alarm|all clear/i);

    expect((await post('/api/ack', { perceptId: alarm?.id }, { 'x-sense-via': 'intent:switch-scan' })).statusCode).toBe(200);
    const after = await state();
    expect(after.acknowledged).toContain(alarm?.id);
    expect(after.actions.at(-1)).toMatchObject({ kind: 'acknowledge', via: 'intent:switch-scan' });
    expect((await post('/api/ack', { perceptId: 'p-nope' })).statusCode).toBe(404);
  });

  it('only lists known world events and scripts', async () => {
    const { post, get } = await start();
    expect((await post('/api/world/event', { name: 'delete-everything' })).statusCode).toBe(400);
    expect((await post('/api/world/play', { script: 'nope' })).statusCode).toBe(400);
    expect((await post('/api/world/play', { script: 'fire' })).statusCode).toBe(200);
    const w = await get('/api/world');
    expect(w.label).toBe('SIMULATED WORLD');
    expect(w.hostnames).toContain('riverside-hall.sim');
  });
});

describe('static web app', () => {
  it('serves the built app, falls back to index.html, and never escapes the directory', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'sense-web-'));
    cleanups.push(() => rm(dir, { recursive: true, force: true }));
    await writeFile(join(dir, 'index.html'), '<!doctype html><title>SENSE</title>');
    await writeFile(join(dir, 'app.js'), 'console.log(1)');
    const { app } = await start({ webDist: dir });
    const root = await app.inject({ method: 'GET', url: '/' });
    expect(root.statusCode).toBe(200);
    expect(root.headers['content-type']).toMatch(/text\/html/);
    expect((await app.inject({ method: 'GET', url: '/app.js' })).headers['content-type']).toMatch(/javascript/);
    expect((await app.inject({ method: 'GET', url: '/some/route' })).body).toContain('<title>SENSE</title>');
    const escape = await app.inject({ method: 'GET', url: '/..%2f..%2f..%2fpackage.json' });
    expect(escape.body).toContain('<title>SENSE</title>');
    expect(escape.headers['x-content-type-options']).toBe('nosniff');
  });

  it('without a build it says so instead of failing', async () => {
    const { app } = await start();
    expect((await app.inject({ method: 'GET', url: '/' })).body).toMatch(/SENSE API is running/);
  });
});

describe('websocket push', () => {
  it('sends state on connect and pushes a verified alarm to the browser quickly', async () => {
    const { app, ctx, post, settle } = await start({ manual: false });
    await app.listen({ port: 0, host: '127.0.0.1' });
    const closeWs = attachWebSocket(app.server, ctx);
    cleanups.push(closeWs);
    const port = (app.server.address() as AddressInfo).port;
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
    const events: ServerEvent[] = [];
    ws.on('message', (m) => events.push(JSON.parse(String(m)) as ServerEvent));
    await new Promise<void>((resolve, reject) => {
      ws.once('open', () => resolve());
      ws.once('error', reject);
    });
    cleanups.push(() => ws.close());
    await settle();
    expect(events[0]?.type).toBe('state');

    await post('/api/arrive', { area: 'riverside' });
    await settle();
    const before = events.length;
    const t0 = performance.now();
    await ctx.world.trigger('fire-alarm');
    let latency = -1;
    for (let i = 0; i < 100 && latency < 0; i++) {
      if (events.slice(before).some((e) => e.type === 'percept' && e.percept.urgency === 4)) latency = performance.now() - t0;
      else await new Promise((r) => setTimeout(r, 5));
    }
    expect(latency).toBeGreaterThan(0);
    expect(latency).toBeLessThan(300); // alert-to-browser latency, localhost
    console.log(`[latency] pushed verified alarm -> websocket client: ${latency.toFixed(1)} ms`);
    expect(events.map((e) => e.type)).toEqual(expect.arrayContaining(['verification', 'disclosure', 'percept']));
  });
});
