import { WorldSim, buildWorldSimHttp } from './index';

/** Standalone world-sim: publishes the simulated agents over HTTP on one port (virtual hosts). */
const port = Number(process.env.SENSE_WORLD_SIM_PORT ?? 8788);
const world = await WorldSim.create({ keyDir: process.env.SENSE_KEY_DIR ?? '.sense/keys' });
const app = await buildWorldSimHttp(world);
setInterval(() => void world.tick(), 1000).unref();
const address = await app.listen({ port, host: '127.0.0.1' });
console.log(`[SIMULATED WORLD] ${world.agents.size} agents at ${address}`);
console.log('Try: curl -H "Host: riverside-hall.sim" ' + address + '/.well-known/sense-card.json');
