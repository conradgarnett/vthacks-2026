/**
 * Judge dashboard. Judges are sighted; the product is audio. This page exists
 * so they can see the latency and reasoning they would otherwise only hear.
 */

const apiBase = (): string => {
  const override = new URLSearchParams(location.search).get("backend");
  return override ?? `${location.protocol}//${location.hostname}:8000`;
};

const el = (id: string) => document.getElementById(id)!;

async function refresh(): Promise<void> {
  try {
    const [health, metrics] = await Promise.all([
      fetch(`${apiBase()}/health`).then((r) => r.json()),
      fetch(`${apiBase()}/metrics`).then((r) => r.json()),
    ]);

    el("health").textContent = JSON.stringify(health, null, 2);

    const rows = Object.entries(metrics as Record<string, { p50: number; p95: number; n: number }>);
    el("metrics").innerHTML = rows.length
      ? rows
          .map(
            ([key, m]) =>
              `<div class="metric"><span>${key}</span><span>p50 <b>${m.p50}ms</b> · p95 ${m.p95}ms · n=${m.n}</span></div>`
          )
          .join("")
      : '<span style="color:var(--muted)">no samples yet — run a scan</span>';
  } catch {
    el("health").textContent = "backend unreachable";
  }
}

void refresh();
setInterval(refresh, 1000);
