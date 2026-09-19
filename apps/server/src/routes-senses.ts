import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { SOUND_LABELS, type LlmImage } from '@sense/providers';
import { DEMO_SOUNDSCAPE, ScriptedSoundscape } from '@sense/hearing';
import { parseBody, viaOf } from './routes-core';
import type { SenseContext } from './context';

const ImageFields = {
  fixture: z.string().max(40).optional(),
  imageBase64: z.string().max(6_000_000).optional(),
  mediaType: z.enum(['image/jpeg', 'image/png', 'image/webp', 'image/gif']).optional(),
};
const SeeBody = z.strictObject(ImageFields);
const AskBody = z.strictObject({ question: z.string().min(1).max(300), ...ImageFields });

function frameOf(b: { fixture?: string | undefined; imageBase64?: string | undefined; mediaType?: LlmImage['mediaType'] | undefined }) {
  return {
    ...(b.fixture ? { fixture: b.fixture } : {}),
    ...(b.imageBase64 ? { image: { mediaType: b.mediaType ?? 'image/jpeg', base64: b.imageBase64 } } : {}),
  };
}

/** Camera-based routes: describe a frame, and ask questions answered from memory + the verified map. */
export function registerVisionRoutes(app: FastifyInstance, ctx: SenseContext): void {
  app.post('/api/see', async (req, reply) => {
    const body = parseBody(SeeBody, req, reply);
    if (!body) return;
    ctx.record('see', viaOf(req), body.fixture ?? 'uploaded frame');
    const percepts = await ctx.vision.see(frameOf(body));
    for (const p of percepts) ctx.broker.publishPercept(p);
    return { percepts };
  });

  app.post('/api/ask', async (req, reply) => {
    const body = parseBody(AskBody, req, reply);
    if (!body) return;
    ctx.record('ask', viaOf(req), body.question.slice(0, 80));
    const hasFrame = body.fixture || body.imageBase64;
    const percepts = await ctx.vision.ask(body.question, hasFrame ? frameOf(body) : undefined);
    for (const p of percepts) ctx.broker.publishPercept(p);
    return { percepts };
  });
}

const SoundBody = z.strictObject({
  label: z.enum(SOUND_LABELS),
  confidence: z.number().min(0).max(1),
  bearingDeg: z.number().min(0).max(360).optional(),
  directionKnown: z.boolean(),
  provider: z.enum(['local-heuristic', 'scripted']),
  at: z.number().min(0).max(86_400).optional(),
});
const SoundscapeBody = z.strictObject({ instant: z.boolean().optional() });

/**
 * Hearing routes. Audio itself is analysed on the user's device (browser) or by the scripted
 * soundscape; only the resulting sound EVENTS (label, confidence, direction) reach this server.
 */
export function registerHearingRoutes(app: FastifyInstance, ctx: SenseContext): void {
  app.post('/api/sound', async (req, reply) => {
    const body = parseBody(SoundBody, req, reply);
    if (!body) return;
    ctx.record('sound', viaOf(req), `${body.label} ${Math.round(body.confidence * 100)}%`);
    const percept = ctx.echo.ingest({
      label: body.label,
      confidence: body.confidence,
      ...(body.directionKnown && body.bearingDeg !== undefined ? { bearingDeg: body.bearingDeg } : {}),
      directionKnown: body.directionKnown && body.bearingDeg !== undefined,
      provider: body.provider,
      at: body.at ?? 0,
    });
    if (percept) ctx.broker.publishPercept(percept);
    return { percept: percept ?? null };
  });

  app.post('/api/soundscape', async (req, reply) => {
    const body = parseBody(SoundscapeBody, req, reply);
    if (!body) return;
    ctx.record('soundscape', viaOf(req), 'simulated soundscape');
    const scape = new ScriptedSoundscape(DEMO_SOUNDSCAPE);
    const emit = (e: ReturnType<ScriptedSoundscape['due']>[number]) => {
      const p = ctx.echo.ingest(e);
      if (p) ctx.broker.publishPercept(p);
    };
    if (body.instant) {
      for (const e of scape.due(1e9)) emit(e);
    } else {
      for (const e of scape.due(1e9)) setTimeout(() => emit(e), e.at * 1000).unref();
    }
    return { events: DEMO_SOUNDSCAPE.length, simulated: true };
  });
}
