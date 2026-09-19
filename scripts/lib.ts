import { ManualClock } from '@sense/protocol';
import {
  SenseContext,
  buildServer,
  registerDeviceRoutes,
  registerHearingRoutes,
  registerTasteRoutes,
  registerVisionRoutes,
  type SceneClient,
} from '@sense/server';

/** ANSI colors. Disabled with NO_COLOR, --no-color, or when output is not a terminal. */
export const useColor =
  !process.env.NO_COLOR && !process.argv.includes('--no-color') && Boolean(process.stdout.isTTY || process.env.FORCE_COLOR);
const c = (code: number) => (s: string) => (useColor ? `\u001b[${code}m${s}\u001b[0m` : s);
export const color = {
  green: c(32),
  red: c(31),
  yellow: c(33),
  blue: c(36),
  dim: c(2),
  bold: c(1),
  magenta: c(35),
};

export const TIER_TAG: Record<string, (s: string) => string> = {
  VERIFIED: color.green,
  INFERRED: color.blue,
  UNVERIFIED: color.yellow,
  REJECTED: color.red,
};

export interface HeadlessEnv {
  ctx: SenseContext;
  client: SceneClient;
  clock: ManualClock;
  skipTime(ms: number): boolean;
  close(): Promise<void>;
}

/**
 * The whole app minus the browser, in one process: the real SENSE server (broker, simulated world,
 * mock AI) driven through its HTTP API with a manual clock. No network, no ports.
 */
export async function createHeadlessEnv(): Promise<HeadlessEnv> {
  const clock = new ManualClock();
  const ctx = await SenseContext.create({ env: {}, clock, online: false });
  const app = await buildServer(ctx, { extra: [registerVisionRoutes, registerHearingRoutes, registerTasteRoutes, registerDeviceRoutes] });
  const client: SceneClient = {
    async get(path) {
      return (await app.inject({ method: 'GET', url: path })).json();
    },
    async post(path, body, via = 'demo') {
      const res = await app.inject({ method: 'POST', url: path, payload: (body ?? {}) as never, headers: { 'x-sense-via': via } });
      return { status: res.statusCode, body: res.json() };
    },
    settle: (ms = 60) => new Promise((r) => setTimeout(r, Math.min(ms, 160))),
  };
  return {
    ctx,
    client,
    clock,
    skipTime: (ms) => (clock.advance(ms), true),
    close: () => app.close(),
  };
}
