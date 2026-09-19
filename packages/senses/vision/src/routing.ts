import { clockPosition, distanceM, relativeBearingDeg, type IndoorMapPayload, type Point2 } from '@sense/protocol';

export interface ExitRoute {
  id: string;
  label: string;
  kind: string;
  /** Distance along the map's edges from the user's nearest node. */
  pathMeters: number;
  /** Straight-line distance and bearing from the user. */
  directMeters: number;
  bearingDeg: number;
  clock: number;
  /** Bearing of the first step of the route (where the user would actually start walking). */
  firstStepBearingDeg: number;
  nodes: string[];
}

const EXIT_KINDS = new Set(['exit']);

/** Dijkstra over the verified map from the node nearest the user. Returns exits nearest first. */
export function exitRoutes(map: IndoorMapPayload, position: Point2, headingDeg: number): ExitRoute[] {
  const nodes = new Map(map.nodes.map((n) => [n.id, n]));
  if (nodes.size === 0) return [];
  let start = map.nodes[0];
  for (const n of map.nodes) if (distanceM(position, n) < distanceM(position, start as (typeof map.nodes)[number])) start = n;
  if (!start) return [];

  const adj = new Map<string, { to: string; w: number }[]>();
  for (const e of map.edges) {
    (adj.get(e.from) ?? adj.set(e.from, []).get(e.from))?.push({ to: e.to, w: e.meters });
    (adj.get(e.to) ?? adj.set(e.to, []).get(e.to))?.push({ to: e.from, w: e.meters });
  }
  const dist = new Map<string, number>([[start.id, 0]]);
  const prev = new Map<string, string>();
  const queue = new Set<string>([start.id]);
  while (queue.size > 0) {
    let u = '';
    let best = Infinity;
    for (const id of queue) {
      const d = dist.get(id) ?? Infinity;
      if (d < best) {
        best = d;
        u = id;
      }
    }
    queue.delete(u);
    for (const { to, w } of adj.get(u) ?? []) {
      const nd = best + w;
      if (nd < (dist.get(to) ?? Infinity)) {
        dist.set(to, nd);
        prev.set(to, u);
        queue.add(to);
      }
    }
  }

  const routes: ExitRoute[] = [];
  for (const n of map.nodes) {
    if (!EXIT_KINDS.has(n.kind) || !dist.has(n.id)) continue;
    const path: string[] = [n.id];
    while (prev.has(path[0] as string)) path.unshift(prev.get(path[0] as string) as string);
    const firstHop = nodes.get(path[1] ?? n.id) ?? n;
    // If the user is not at the start node, the first step is toward it.
    const target = distanceM(position, start) > 1.5 ? start : firstHop;
    const bearing = Math.round(relativeBearingDeg(position, n, headingDeg));
    routes.push({
      id: n.id,
      label: n.label,
      kind: n.kind,
      pathMeters: Math.round((dist.get(n.id) ?? 0) * 10) / 10,
      directMeters: Math.round(distanceM(position, n) * 10) / 10,
      bearingDeg: bearing,
      clock: clockPosition(bearing),
      firstStepBearingDeg: Math.round(relativeBearingDeg(position, target, headingDeg)),
      nodes: path,
    });
  }
  return routes.sort((a, b) => a.pathMeters - b.pathMeters || a.directMeters - b.directMeters);
}

export function angularDiff(a: number, b: number): number {
  return Math.abs(((((a - b) % 360) + 540) % 360) - 180);
}
