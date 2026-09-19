import { useEffect, useState } from 'react';
import { SimBadge } from './Badges';

interface DemoInfo {
  scenes: { id: number; title: string }[];
  running: boolean;
  last: { id: number; title: string; checks: { name: string; ok: boolean; detail?: string }[] }[];
  simulatedTimeSkippedMs: number;
}

/** Scripted demo scenes (only shown when the server runs in demo mode). Everything they do is simulated. */
export function DemoPanel({
  get,
  post,
  refreshKey,
}: {
  get: (p: string) => Promise<unknown>;
  post: (p: string, b?: unknown) => Promise<unknown>;
  refreshKey: number;
}) {
  const [info, setInfo] = useState<DemoInfo | undefined>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    get('/api/demo/scenes')
      .then((r) => {
        // Outside demo mode the server has no such route: anything that is not the expected shape hides the panel.
        const ok = typeof r === 'object' && r !== null && Array.isArray((r as DemoInfo).scenes);
        if (live) setInfo(ok ? (r as DemoInfo) : undefined);
      })
      .catch(() => live && setInfo(undefined)); // not in demo mode: panel stays hidden
    return () => {
      live = false;
    };
  }, [get, refreshKey, busy]);

  if (!info) return null;
  const run = async (path: string, body?: unknown) => {
    setBusy(true);
    await post(path, body);
    setBusy(false);
  };
  const failed = info.last.flatMap((r) => r.checks.filter((c) => !c.ok));

  return (
    <section aria-labelledby="demo-h" className="panel">
      <h2 id="demo-h">
        Demo scenes <SimBadge />
      </h2>
      <p className="meta">
        Scripted, simulated scenes with built-in assertions. Manual mode is the controls above; these play them for you.
      </p>
      <div className="toolbar" role="group" aria-label="Demo scenes">
        {info.scenes.map((s) => (
          <button key={s.id} type="button" disabled={busy || info.running} onClick={() => void run(`/api/demo/scene/${s.id}`)}>
            Scene {s.id}
          </button>
        ))}
        <button type="button" className="primary" disabled={busy || info.running} onClick={() => void run('/api/demo/autoplay')}>
          Auto-play all
        </button>
        <button type="button" onClick={() => void run('/api/demo/skip-time', { seconds: 120 })}>
          Skip 2 minutes (simulated time)
        </button>
      </div>
      <ol className="steps" aria-label="Scene titles">
        {info.scenes.map((s) => (
          <li key={s.id}>
            {s.id}. {s.title}
          </li>
        ))}
      </ol>
      <p role="status">
        {info.running || busy
          ? 'Running…'
          : info.last.length
            ? `Last run: ${info.last.reduce((n, r) => n + r.checks.length, 0)} checks, ${failed.length} failed.`
            : 'Not run yet.'}
        {info.simulatedTimeSkippedMs ? ` SIMULATED TIME SKIP: ${Math.round(info.simulatedTimeSkippedMs / 1000)} s.` : ''}
      </p>
    </section>
  );
}
