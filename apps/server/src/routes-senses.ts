import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import type { LlmImage } from '@sense/providers';
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
