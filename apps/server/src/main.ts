import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { attachWebSocket, buildServer } from './app';
import { OffsetClock } from '@sense/protocol';
import { buildWorldSimHttp } from '@sense/world-sim';
import { registerDemoRoutes } from './demo-routes';
import { registerDeviceRoutes, registerHearingRoutes, registerTasteRoutes, registerVisionRoutes } from './routes-senses';
import { SenseContext } from './context';
import { checkOnline } from './online';

/** Local SENSE server: broker + simulated world + API for the web app. */
const here = dirname(fileURLToPath(import.meta.url));
const online = await checkOnline();
const demo = process.env.SENSE_DEMO === '1';
const ctx = await SenseContext.create({
  keyDir: process.env.SENSE_KEY_DIR ?? '.sense/keys',
  online,
  ...(demo ? { clock: new OffsetClock() } : {}),
});
const app = await buildServer(ctx, {
  webDist: join(here, '../../web/dist'),
  extra: [registerVisionRoutes, registerHearingRoutes, registerTasteRoutes, registerDeviceRoutes, ...(demo ? [registerDemoRoutes] : [])],
});
const stopBackground = ctx.startBackground();

const wanted = Number(process.env.SENSE_SERVER_PORT ?? 8787);
let address: string;
try {
  address = await app.listen({ port: wanted, host: '127.0.0.1' });
} catch {
  address = await app.listen({ port: 0, host: '127.0.0.1' }); // port in use: pick a free one
}
const closeWs = attachWebSocket(app.server, ctx);

// A window onto the same simulated agents, one hostname each (curl -H "Host: riverside-hall.sim" ...).
const worldHttp = await buildWorldSimHttp(ctx.world);
const worldPort = Number(process.env.SENSE_WORLD_SIM_PORT ?? 8788);
let worldAddress: string;
try {
  worldAddress = await worldHttp.listen({ port: worldPort, host: '127.0.0.1' });
} catch {
  worldAddress = await worldHttp.listen({ port: 0, host: '127.0.0.1' });
}

console.log(`SENSE server listening at ${address}`);
console.log(`  mode: ${ctx.mode.world} | ${ctx.mode.ans} | ${ctx.mode.ai} | ${online ? 'online' : 'offline'}`);
console.log(
  `  [SIMULATED WORLD] agents by hostname at ${worldAddress}${demo ? ' | demo mode: scripted scenes and simulated time skips enabled' : ''}`,
);
console.log('  This is a prototype, not a medical device or a certified safety system.');

const shutdown = async () => {
  stopBackground();
  closeWs();
  await worldHttp.close();
  await app.close();
  process.exit(0);
};
process.on('SIGINT', () => void shutdown());
process.on('SIGTERM', () => void shutdown());
