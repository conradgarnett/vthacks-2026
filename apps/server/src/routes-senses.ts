import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { SOUND_LABELS, type LlmImage } from '@sense/providers';
import { DEMO_SOUNDSCAPE, ScriptedSoundscape } from '@sense/hearing';
import type { MenuItemInput, MenuSource } from '@sense/taste';
import type { MenuAllergensPayload } from '@sense/protocol';
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

const TasteBody = z.strictObject({
  fqdn: z.string().max(253).optional(),
  itemId: z.string().max(64).optional(),
  labelText: z.string().max(2000).optional(),
  text: z.string().max(1000).optional(),
  ...ImageFields,
});

/**
 * TasteLens routes. The user's declared allergens are read locally from the profile; the only
 * thing sent to a publisher is the standard, minimal menu-allergens query (no allergens, no profile).
 */
export function registerTasteRoutes(app: FastifyInstance, ctx: SenseContext): void {
  app.get('/api/menu', async () => ({
    menus: ctx.broker.latestFor('menu-allergens').map((d) => ({
      fqdn: d.fqdn,
      label: d.label,
      tier: d.tier,
      items: (d.payload as MenuAllergensPayload).items.map((i) => ({ id: i.id, name: i.name })),
    })),
  }));

  app.post('/api/taste', async (req, reply) => {
    const body = parseBody(TasteBody, req, reply);
    if (!body) return;
    ctx.record('taste', viaOf(req), body.itemId ?? body.fixture ?? 'label/photo');

    let menu: { source: MenuSource; item: MenuItemInput } | undefined;
    if (body.itemId) {
      const fqdn = body.fqdn ?? 'bella-cucina.sim';
      let rec = ctx.broker.latest(fqdn, 'menu-allergens');
      if (!rec) {
        await ctx.broker.connect(fqdn).catch(() => undefined);
        rec = (await ctx.broker.query(fqdn, 'menu-allergens')) ?? undefined;
      }
      const item = (rec?.payload as MenuAllergensPayload | undefined)?.items.find((i) => i.id === body.itemId);
      if (!rec || !item) return reply.code(404).send({ error: 'no such menu item from a usable source' });
      menu = {
        source: {
          fqdn: rec.fqdn,
          label: rec.label,
          tier: rec.tier,
          simulated: rec.simulated,
          agentVersion: rec.source.agentVersion,
          verifiedAt: rec.source.verifiedAt,
          evidence: rec.source.evidence,
        },
        item,
      };
    }
    const hasPhoto = body.fixture || body.imageBase64 || body.text;
    const result = await ctx.taste.analyze({
      ...(menu ? { menu } : {}),
      ...(body.labelText ? { labelText: body.labelText } : {}),
      ...(hasPhoto ? { photo: { ...frameOf(body), ...(body.text ? { text: body.text } : {}) } } : {}),
    });
    for (const p of result.percepts) ctx.broker.publishPercept(p);
    return { percepts: result.percepts, degraded: result.degraded ?? null };
  });
}

const LiftBody = z.strictObject({ floor: z.number().int().min(0).max(2) });

/**
 * Device control through the SAME verified channel as everything else: the lift agent is verified
 * first, the request carries only the minimum scope, and the answer is shown with its provenance.
 */
export function registerDeviceRoutes(app: FastifyInstance, ctx: SenseContext): void {
  app.post('/api/lift', async (req, reply) => {
    const body = parseBody(LiftBody, req, reply);
    if (!body) return;
    const via = viaOf(req);
    ctx.record('lift', via, `call to floor ${body.floor}`);
    const fqdn = 'hall-lifts.sim';
    if (!ctx.broker.verifications().some((v) => v.fqdn === fqdn && v.result.outcome !== 'REJECTED'))
      await ctx.broker.connect(fqdn).catch(() => undefined);
    const rec = await ctx.broker.query(fqdn, 'device-control', { action: 'call', device: 'lift-1', floor: body.floor });
    const result = (rec?.payload as { result?: { ok: boolean; message: string } } | undefined)?.result;
    const source = rec?.source;
    const tierWord = rec ? rec.tier.charAt(0) + rec.tier.slice(1).toLowerCase() : 'Unverified';
    const label = rec?.label ?? 'Hall Lifts';
    const ok = result?.ok === true;
    const percept = ctx.broker.publishPercept({
      id: ctx.broker.nextPerceptId(),
      timestamp: new Date(ctx.clock.now()).toISOString(),
      sense: 'touch',
      kind: 'action',
      urgency: ok ? 1 : 2,
      short: !rec
        ? `Lift not reachable. Unverified, ${label}.`
        : ok
          ? `Lift called to floor ${body.floor}. ${tierWord}, ${label}.`
          : `Lift refused the call. ${tierWord}, ${label}.`,
      long: !rec
        ? 'The lift agent could not be reached or verified, so nothing was sent.'
        : `${label} (${rec.tier}) answered: ${result?.message ?? 'no result'}`,
      provenance: {
        tier: rec?.tier ?? 'UNVERIFIED',
        source: fqdn,
        sourceLabel: label,
        ...(source
          ? { agentVersion: source.agentVersion, verifiedAt: source.verifiedAt, evidence: source.evidence }
          : { evidence: ['lift agent not reachable'] }),
      },
      ...(rec?.simulated ? { simulated: true } : {}),
    });
    return { ok, percept };
  });
}
