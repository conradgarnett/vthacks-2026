import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import type { StateSnapshot } from '@sense/protocol';
import { OffsetClock, ManualClock } from '@sense/protocol';
import type { SenseContext } from './context';
import { parseBody } from './routes-core';
import { SCENES, runScenes, type SceneClient, type SceneResult } from './scenes';

const SkipBody = z.strictObject({ seconds: z.number().min(1).max(3600) });

/**
 * Demo routes (only registered when SENSE_DEMO=1): play one scripted scene, auto-play all of them,
 * or skip SIMULATED time so stale-data behaviour can be shown without waiting.
 * Scenes run through the server's own HTTP API in-process, exactly as `demo:verify` does.
 */
export function registerDemoRoutes(app: FastifyInstance, ctx: SenseContext): void {
  const client: SceneClient = {
    async get(path) {
      return (await app.inject({ method: 'GET', url: path })).json();
    },
    async post(path, body, via = 'demo') {
      const res = await app.inject({ method: 'POST', url: path, payload: (body ?? {}) as never, headers: { 'x-sense-via': via } });
      return { status: res.statusCode, body: res.json() };
    },
    settle: (ms = 300) => new Promise((r) => setTimeout(r, ms)),
  };
  const skipTime = (ms: number): boolean => {
    const clock = ctx.clock;
    if (clock instanceof OffsetClock || clock instanceof ManualClock) {
      clock.advance(ms);
      return true;
    }
    return false;
  };
  let running = false;
  let last: SceneResult[] = [];

  app.get('/api/demo/scenes', async () => ({
    scenes: SCENES.map((s) => ({ id: s.id, title: s.title })),
    running,
    last,
    simulatedTimeSkippedMs: ctx.clock instanceof OffsetClock ? ctx.clock.skippedMs : 0,
  }));

  app.post<{ Params: { n: string } }>('/api/demo/scene/:n', async (req, reply) => {
    const n = Number(req.params.n);
    if (!SCENES.some((s) => s.id === n)) return reply.code(404).send({ error: 'no such scene' });
    if (running) return reply.code(409).send({ error: 'a demo is already running' });
    running = true;
    try {
      ctx.record('demo', 'demo', `scene ${n}`);
      last = await runScenes({ ctx, client, skipTime }, {}, [n]);
      return { results: last };
    } finally {
      running = false;
    }
  });

  app.post('/api/demo/autoplay', async (_req, reply) => {
    if (running) return reply.code(409).send({ error: 'a demo is already running' });
    running = true;
    ctx.record('demo', 'demo', 'auto-play all scenes');
    void runScenes({ ctx, client, skipTime }, {})
      .then((r) => (last = r))
      .finally(() => (running = false));
    return { started: true, scenes: SCENES.length };
  });

  app.post('/api/demo/skip-time', async (req, reply) => {
    const body = parseBody(SkipBody, req, reply);
    if (!body) return;
    const ok = skipTime(body.seconds * 1000);
    if (!ok) return reply.code(400).send({ error: 'this clock cannot skip simulated time' });
    ctx.record('skip-time', 'demo', `SIMULATED TIME SKIP: ${body.seconds}s`);
    ctx.broker.checkFreshness();
    ctx.updateScent();
    return { ok: true, label: 'SIMULATED TIME SKIP', seconds: body.seconds };
  });
}

export type { StateSnapshot };
