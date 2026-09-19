import type { AddressInfo } from 'node:net';
import WebSocket from 'ws';
import type { ServerEvent } from '@sense/protocol';
import { SenseContext, attachWebSocket, buildServer } from '@sense/server';

/**
 * Alert-to-render latency for a pushed VERIFIED alarm, measured locally and in-process:
 *   broker:  world event -> signed push -> verify -> sanitize -> percept emitted
 *   browser: world event -> percept delivered to a WebSocket client (before it is drawn)
 * "Render" in a browser adds paint time, which is not measured here. 15 fresh runs each.
 */
const RUNS = 15;
const broker: number[] = [];
const socket: number[] = [];

for (let i = 0; i < RUNS; i++) {
  const ctx = await SenseContext.create({ env: {}, online: false });
  const app = await buildServer(ctx);
  await app.listen({ port: 0, host: '127.0.0.1' });
  const closeWs = attachWebSocket(app.server, ctx);
  const ws = new WebSocket(`ws://127.0.0.1:${(app.server.address() as AddressInfo).port}/ws`);
  await new Promise<void>((r) => ws.once('open', () => r()));
  await ctx.broker.arrive('riverside');
  await new Promise((r) => setTimeout(r, 60));

  let tBroker = 0;
  let tSocket = 0;
  ctx.broker.on('percept', (p) => {
    if (p.urgency === 4 && !tBroker) tBroker = performance.now();
  });
  ws.on('message', (m) => {
    const e = JSON.parse(String(m)) as ServerEvent;
    if (e.type === 'percept' && e.percept.urgency === 4 && !tSocket) tSocket = performance.now();
  });
  const t0 = performance.now();
  await ctx.world.trigger('fire-alarm');
  for (let k = 0; k < 400 && !(tBroker && tSocket); k++) await new Promise((r) => setTimeout(r, 2));
  if (i >= 3) {
    // the first runs warm up the JIT and are reported separately
    broker.push(tBroker - t0);
    socket.push(tSocket - t0);
  }
  ws.close();
  closeWs();
  await app.close();
}

const stat = (v: number[]) => {
  const s = [...v].sort((a, b) => a - b);
  return `median ${s[Math.floor(s.length / 2)]?.toFixed(1)} ms, p95 ${s[Math.floor(s.length * 0.95)]?.toFixed(1)} ms, max ${s[s.length - 1]?.toFixed(1)} ms`;
};
console.log(`Pushed VERIFIED alarm, ${broker.length} warm runs (first 3 discarded as JIT warm-up), localhost, in-process:`);
console.log(`  world event -> percept emitted by the broker: ${stat(broker)}`);
console.log(`  world event -> percept received by a WebSocket client: ${stat(socket)}`);
console.log('  Target: under 300 ms. Browser paint time is not included.');
process.exit(0);
