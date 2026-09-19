import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * npm run demo [-- --auto] [-- --open] [-- --dev] [-- --build]
 *  - starts the local SENSE server (broker + simulated world, in demo mode) and serves the web app
 *  - prints the URL (and opens it with --open)
 *  - manual mode by default; --auto plays all scripted scenes for you
 *  - --dev runs the Vite dev server (hot reload) instead of serving the built app
 * Everything in the demo is SIMULATED.
 */
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const args = new Set(process.argv.slice(2));
const require = createRequire(import.meta.url);
const viteBin = join(dirname(require.resolve('vite/package.json')), 'bin', 'vite.js');
const children: ChildProcess[] = [];

function shutdown(code = 0): never {
  for (const c of children) c.kill();
  process.exit(code);
}
process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

if (!args.has('--dev') && (args.has('--build') || !existsSync(join(root, 'apps/web/dist/index.html')))) {
  console.log('Building the web app…');
  const b = spawnSync(process.execPath, [viteBin, 'build'], { cwd: join(root, 'apps/web'), stdio: 'inherit' });
  if (b.status !== 0) {
    console.error('Web build failed.');
    process.exit(1);
  }
}

const server = spawn(process.execPath, ['--import', 'tsx', 'apps/server/src/main.ts'], {
  cwd: root,
  env: { ...process.env, SENSE_DEMO: '1' },
  stdio: ['ignore', 'pipe', 'inherit'],
});
children.push(server);
server.on('exit', (code) => shutdown(code ?? 0));

let serverUrl = '';
let buffer = '';
server.stdout?.on('data', (chunk: Buffer) => {
  const text = chunk.toString();
  process.stdout.write(text);
  buffer += text;
  const m = /SENSE server listening at (http:\/\/\S+)/.exec(buffer);
  if (m && !serverUrl) {
    serverUrl = m[1] as string;
    void ready(serverUrl);
  }
});

function openBrowser(url: string): void {
  const cmd =
    process.platform === 'win32'
      ? ['cmd', ['/c', 'start', '', url]]
      : process.platform === 'darwin'
        ? ['open', [url]]
        : ['xdg-open', [url]];
  spawn(cmd[0] as string, cmd[1] as string[], { stdio: 'ignore', detached: true }).unref();
}

async function ready(url: string): Promise<void> {
  let appUrl = url;
  if (args.has('--dev')) {
    const port = new URL(url).port;
    const dev = spawn(process.execPath, [viteBin], {
      cwd: join(root, 'apps/web'),
      env: { ...process.env, SENSE_SERVER_PORT: port },
      stdio: ['ignore', 'pipe', 'inherit'],
    });
    children.push(dev);
    appUrl = await new Promise<string>((resolve) => {
      dev.stdout?.on('data', (d: Buffer) => {
        // eslint-disable-next-line no-control-regex -- stripping ANSI escape sequences is the point
        const m = /Local:\s+(http:\/\/\S+)/.exec(d.toString().replace(/\u001b\[[0-9;]*m/g, ''));
        if (m) resolve(m[1] as string);
      });
    });
  }
  console.log(`\n  SENSE demo is ready:  ${appUrl}`);
  console.log('  Everything is simulated (SIMULATED WORLD, MOCK AI, ANS-modeled identity). Press Ctrl+C to stop.');
  console.log(
    `  Manual mode: use the "Simulated world controls" and "Demo scenes" panels.${args.has('--auto') ? '' : '  Add --auto to play all scenes.'}`,
  );
  if (args.has('--open')) openBrowser(appUrl);
  if (args.has('--auto')) {
    try {
      const res = await fetch(`${url}/api/demo/autoplay`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
      console.log(res.ok ? '  Auto-play started: watch the feed.' : `  Auto-play could not start (${res.status}).`);
    } catch (err) {
      console.log(`  Auto-play could not start: ${err instanceof Error ? err.message : err}`);
    }
  }
}
