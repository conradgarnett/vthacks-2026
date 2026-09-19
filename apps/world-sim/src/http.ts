import Fastify, { type FastifyInstance, type FastifyRequest } from 'fastify';
import type { WorldSim } from './world';

/**
 * HTTP view of the simulated world, for inspection with curl or a browser.
 *
 * "Domains": every agent is reachable at its own hostname. Either send the request to this server
 * with `Host: riverside-hall.sim` (virtual hosting, like `curl --resolve`), or use the explicit
 * path form `/agents/riverside-hall.sim/...`. The SENSE broker itself talks to the same agents
 * through the in-process hostname map (`SimTransport`); this server is a window onto them.
 */
export async function buildWorldSimHttp(world: WorldSim): Promise<FastifyInstance> {
  const app = Fastify({ logger: false });

  const hostOf = (req: FastifyRequest): string | undefined => (req.headers.host ?? '').split(':')[0]?.toLowerCase();
  const agentFor = (fqdn: string | undefined) => (fqdn ? world.agents.get(fqdn) : undefined);

  app.get('/', async () => ({
    label: 'SIMULATED WORLD',
    note: 'Every agent here is simulated. Nothing describes a real place.',
    hostnames: [...world.agents.keys()].sort(),
  }));

  app.get('/.well-known/sense-card.json', async (req, reply) => {
    const agent = agentFor(hostOf(req));
    if (!agent) return reply.code(404).send({ error: 'unknown host' });
    return agent.identity.signedCard;
  });

  app.post('/rpc', async (req, reply) => {
    const agent = agentFor(hostOf(req));
    if (!agent) return reply.code(404).send({ error: 'unknown host' });
    try {
      return await agent.handle(req.body);
    } catch {
      return reply.code(503).send({ error: 'agent unreachable' });
    }
  });

  app.post<{ Params: { fqdn: string } }>('/agents/:fqdn/rpc', async (req, reply) => {
    const agent = agentFor(req.params.fqdn.toLowerCase());
    if (!agent) return reply.code(404).send({ error: 'unknown agent' });
    try {
      return await agent.handle(req.body);
    } catch {
      return reply.code(503).send({ error: 'agent unreachable' });
    }
  });

  app.get<{ Params: { fqdn: string } }>('/agents/:fqdn/events', async (req, reply) => {
    const agent = agentFor(req.params.fqdn.toLowerCase());
    if (!agent) return reply.code(404).send({ error: 'unknown agent' });
    reply.hijack();
    reply.raw.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    });
    const off = agent.addSink((raw) => reply.raw.write(`data: ${JSON.stringify(raw)}\n\n`));
    req.raw.on('close', off);
  });

  // Sim control (demo only): fire scripted events by name.
  app.post<{ Params: { name: string }; Querystring: { arg?: string } }>('/sim/event/:name', async (req, reply) => {
    try {
      await world.trigger(req.params.name, req.query.arg === undefined ? undefined : Number(req.query.arg));
      return { ok: true };
    } catch (err) {
      return reply.code(400).send({ error: err instanceof Error ? err.message : 'failed' });
    }
  });

  return app;
}
