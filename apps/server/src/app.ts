import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, resolve } from 'node:path';
import type { Server } from 'node:http';
import Fastify, { type FastifyInstance } from 'fastify';
import { WebSocketServer } from 'ws';
import type { ServerEvent } from '@sense/protocol';
import type { SenseContext } from './context';
import { registerCoreRoutes } from './routes-core';

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.webmanifest': 'application/manifest+json',
};

export interface ServerOptions {
  /** Directory with the built web app. If it exists it is served at "/". */
  webDist?: string;
  /** Extra route registrars added by sense modules. */
  extra?: Array<(app: FastifyInstance, ctx: SenseContext) => void>;
}

/** The local SENSE HTTP API. It binds to localhost only: this is the user's own device. */
export async function buildServer(ctx: SenseContext, opts: ServerOptions = {}): Promise<FastifyInstance> {
  const app = Fastify({ logger: false, bodyLimit: 8 * 1024 * 1024 });

  // Same-origin only: no CORS headers are sent, and mutating requests must be JSON.
  app.addHook('onSend', async (_req, reply) => {
    reply.header('x-content-type-options', 'nosniff');
    reply.header('cache-control', 'no-store');
  });

  registerCoreRoutes(app, ctx);
  for (const register of opts.extra ?? []) register(app, ctx);

  const dist = opts.webDist ? resolve(opts.webDist) : undefined;
  if (dist && existsSync(join(dist, 'index.html'))) {
    app.get('/*', async (req, reply) => {
      const url = (req.raw.url ?? '/').split('?')[0] ?? '/';
      const rel = normalize(decodeURIComponent(url)).replace(/^([/\\])+/, '');
      const file = resolve(dist, rel === '' ? 'index.html' : rel);
      const inside = file.startsWith(dist);
      const target = inside && existsSync(file) && extname(file) ? file : join(dist, 'index.html');
      reply.header('content-type', MIME[extname(target)] ?? 'application/octet-stream');
      return reply.send(await readFile(target));
    });
  } else {
    app.get('/', async (_req, reply) =>
      reply.type('text/plain').send('SENSE API is running. Build the web app (npm run build) or use npm run demo for the UI.'),
    );
  }
  return app;
}

/** Push every server event to connected browsers at /ws. Returns a function that closes the socket server. */
export function attachWebSocket(server: Server, ctx: SenseContext): () => void {
  const wss = new WebSocketServer({ server, path: '/ws' });
  const off = ctx.onEvent((e: ServerEvent) => {
    const data = JSON.stringify(e);
    for (const client of wss.clients) if (client.readyState === client.OPEN) client.send(data);
  });
  wss.on('connection', (socket) => {
    socket.send(JSON.stringify({ type: 'state', state: ctx.snapshot() } satisfies ServerEvent));
  });
  return () => {
    off();
    for (const c of wss.clients) c.terminate();
    wss.close();
  };
}
