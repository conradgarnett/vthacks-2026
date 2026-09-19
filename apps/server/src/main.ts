import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { attachWebSocket, buildServer } from './app';
import { SenseContext } from './context';
import { checkOnline } from './online';

/** Local SENSE server: broker + simulated world + API for the web app. */
const here = dirname(fileURLToPath(import.meta.url));
const online = await checkOnline();
const ctx = await SenseContext.create({ keyDir: process.env.SENSE_KEY_DIR ?? '.sense/keys', online });
const app = await buildServer(ctx, { webDist: join(here, '../../web/dist') });
const stopBackground = ctx.startBackground();

const wanted = Number(process.env.SENSE_SERVER_PORT ?? 8787);
let address: string;
try {
  address = await app.listen({ port: wanted, host: '127.0.0.1' });
} catch {
  address = await app.listen({ port: 0, host: '127.0.0.1' }); // port in use: pick a free one
}
const closeWs = attachWebSocket(app.server, ctx);

console.log(`SENSE server listening at ${address}`);
console.log(`  mode: ${ctx.mode.world} | ${ctx.mode.ans} | ${ctx.mode.ai} | ${online ? 'online' : 'offline'}`);
console.log('  This is a prototype, not a medical device or a certified safety system.');

const shutdown = async () => {
  stopBackground();
  closeWs();
  await app.close();
  process.exit(0);
};
process.on('SIGINT', () => void shutdown());
process.on('SIGTERM', () => void shutdown());
