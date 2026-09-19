import type { FastifyInstance, FastifyRequest } from 'fastify';
import { z } from 'zod';
import { PERSONA_IDS, SITUATIONAL_PRESETS, SensoryProfileSchema, SignedProfileSchema, type PersonaId } from '@sense/protocol';
import { exportProfile, importProfile } from '@sense/core';
import { exportSpki } from '@sense/identity';
import { ATTACKERS, SCRIPTS } from '@sense/world-sim';
import type { SenseContext } from './context';

export const WORLD_EVENTS = [
  'fire-alarm',
  'clear-alarm',
  'smoke',
  'freeze-sensors',
  'city-aqi',
  'spoof-false-alarm',
  'flood',
  'mute-alarm-feed',
  'activate-attackers',
] as const;

/** How an action was performed: "pointer" by default, or "intent:<driver>" from an InputDevice. */
export function viaOf(req: FastifyRequest): string {
  const v = req.headers['x-sense-via'];
  const via = Array.isArray(v) ? v[0] : v;
  return via && /^[a-z0-9:_-]{1,40}$/i.test(via) ? via : 'pointer';
}

const AreaBody = z.strictObject({ area: z.string().min(1).max(64) });
const PersonaBody = z.strictObject({ personaId: z.enum(PERSONA_IDS) });
const CustomBody = z.strictObject({ profile: SensoryProfileSchema });
const SituationBody = z.strictObject({ preset: z.enum(SITUATIONAL_PRESETS).nullable() });
const AllergensBody = z.strictObject({ allergens: z.array(z.string().min(1).max(40)).max(32) });
const AckBody = z.strictObject({ perceptId: z.string().min(1).max(64) });
const EventBody = z.strictObject({ name: z.enum(WORLD_EVENTS), arg: z.number().finite().optional() });
const PlayBody = z.strictObject({ script: z.string().min(1).max(32) });

/** Parse a body with Zod; on failure reply 400 with a short reason and return undefined. */
export function parseBody<T extends z.ZodType>(
  schema: T,
  req: FastifyRequest,
  reply: { code(n: number): { send(b: unknown): unknown } },
): z.infer<T> | undefined {
  const r = schema.safeParse(req.body);
  if (r.success) return r.data;
  reply
    .code(400)
    .send({ error: 'invalid request', issues: r.error.issues.slice(0, 3).map((i) => `${i.path.join('.') || 'body'}: ${i.message}`) });
  return undefined;
}

export function registerCoreRoutes(app: FastifyInstance, ctx: SenseContext): void {
  app.get('/api/state', async () => ctx.snapshot());

  app.get('/api/verifications/:fqdn', async (req: FastifyRequest<{ Params: { fqdn: string } }>, reply) => {
    const v = ctx.snapshot().verifications.find((x) => x.fqdn === req.params.fqdn);
    return v ?? reply.code(404).send({ error: 'no verification for that agent yet' });
  });

  app.post('/api/arrive', async (req, reply) => {
    const body = parseBody(AreaBody, req, reply);
    if (!body) return;
    ctx.record('arrive', viaOf(req), body.area);
    const views = await ctx.broker.arrive(body.area);
    return { agents: views.map((v) => ({ fqdn: v.fqdn, outcome: v.result.outcome })) };
  });

  // ── Profile ─────────────────────────────────────────────────────────────────────────────

  app.post('/api/profile/persona', async (req, reply) => {
    const body = parseBody(PersonaBody, req, reply);
    if (!body) return;
    const profile = ctx.profiles.setPersona(body.personaId as PersonaId);
    ctx.record('persona', viaOf(req), body.personaId);
    return { profile };
  });

  app.post('/api/profile/custom', async (req, reply) => {
    const body = parseBody(CustomBody, req, reply);
    if (!body) return;
    ctx.record('profile-custom', viaOf(req), body.profile.name);
    return { profile: ctx.profiles.setCustom(body.profile) };
  });

  app.post('/api/profile/situation', async (req, reply) => {
    const body = parseBody(SituationBody, req, reply);
    if (!body) return;
    ctx.record('situation', viaOf(req), body.preset ?? 'none');
    return { profile: ctx.profiles.setSituation(body.preset) };
  });

  app.post('/api/profile/allergens', async (req, reply) => {
    const body = parseBody(AllergensBody, req, reply);
    if (!body) return;
    ctx.record('allergens', viaOf(req), `${body.allergens.length} declared`);
    return { profile: ctx.profiles.setAllergens(body.allergens) };
  });

  app.get('/api/profile/export', async () => exportProfile(ctx.profiles.current(), ctx.ownerKeys));

  app.post('/api/profile/import', async (req, reply) => {
    const parsed = SignedProfileSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'not a signed SENSE profile' });
    try {
      // Only files signed by this device's own owner key are accepted.
      const profile = await importProfile(parsed.data, await exportSpki(ctx.ownerKeys.publicKey));
      ctx.record('profile-import', viaOf(req), profile.name);
      return { profile: ctx.profiles.setCustom(profile) };
    } catch (err) {
      return reply.code(400).send({ error: err instanceof Error ? err.message : 'import failed' });
    }
  });

  // ── Acknowledge ─────────────────────────────────────────────────────────────────────────

  app.post('/api/ack', async (req, reply) => {
    const body = parseBody(AckBody, req, reply);
    if (!body) return;
    const via = viaOf(req);
    if (!ctx.broker.acknowledge(body.perceptId, via)) return reply.code(404).send({ error: 'no such percept' });
    ctx.record('acknowledge', via, body.perceptId);
    return { ok: true };
  });

  // ── Simulated world controls (demo only) ────────────────────────────────────────────────

  app.get('/api/world', async () => ({
    label: 'SIMULATED WORLD',
    hostnames: [...ctx.world.agents.keys()].sort(),
    events: WORLD_EVENTS,
    scripts: Object.keys(SCRIPTS),
    attackers: ATTACKERS,
  }));

  app.post('/api/world/event', async (req, reply) => {
    const body = parseBody(EventBody, req, reply);
    if (!body) return;
    ctx.record('world-event', viaOf(req), body.name);
    await ctx.world.trigger(body.name, body.arg);
    return { ok: true };
  });

  app.post('/api/world/play', async (req, reply) => {
    const body = parseBody(PlayBody, req, reply);
    if (!body || !(body.script in SCRIPTS)) return reply.code(400).send({ error: 'unknown script' });
    ctx.world.play(body.script);
    await ctx.world.tick();
    return { ok: true };
  });
}
