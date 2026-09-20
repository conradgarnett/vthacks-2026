/**
 * A top-down blueprint of what the last scan saw, for a sighted helper.
 *
 * Each scan is drawn from above: the user at the bottom, the camera's field
 * of view as a wedge, everything the scan saw at its direction and distance,
 * things that obstruct in red. The floor is a grid of cells: unobstructed
 * where the scan looked and found nothing in the way up to a thing, obstructed
 * where that thing stands, unknown where the scan could not see. The walkway
 * strip ahead is labelled clear or blocked by the rule the spoken scan uses.
 *
 * Walls are drawn as walls: a line across the directions the camera saw them
 * in. A wall has no height to judge distance by, so its distance comes from
 * where it meets the floor, given the camera's height above the floor; when
 * that floor line is out of the frame the wall sits at the edge of the map
 * with a question mark. The floor beyond a wall is unknown.
 *
 * A remembered place is drawn from its saved views, turned to line up on the
 * things they share; a view that shares too little stays out of the drawing
 * rather than being placed by guesswork.
 *
 * Nothing here is spoken. The blind user hears the scan itself; this is the
 * same information for someone who can look.
 */

import type { PlaceEvent } from "./ws";

export type MapItem = {
  label: string;
  azimuth_deg: number;
  distance_m: number | null;
  obstacle?: boolean;
  scale?: "small" | "medium" | "large";
  frames?: number;
  confidence?: number;
  /** The box in the frame, normalized, when the scan had one. */
  box?: [number, number, number, number] | null;
  /** For a wall: the directions its left and right edges were seen at. */
  span?: [number, number];
  /** True when the distance was worked out from where it meets the floor. */
  estimated?: boolean;
};
type SceneLayout = { id: string; at: number; layout?: MapItem[]; aspect?: number | null };
type PlaceLayouts = { id: string; name: string; scenes: SceneLayout[] };
type Memory = {
  places: PlaceLayouts[];
  current: { place_id: string; name: string; score: number; fresh: boolean } | null;
};
export type Cell = "clear" | "blocked" | "unknown";

// The same field of view and walkway as the backend: a 66 degree camera, and
// a strip one metre wide and three long straight ahead.
const RANGE_M = 6;
const CELL_M = 0.5;
const HALF_FOV_DEG = 33;
const WALK_WIDTH_M = 1.0;
const WALK_DISTANCE_M = 3.0;
// How wide a thing is on the floor, by its size tier.
const HALF_WIDTH_M: Record<string, number> = { small: 0.15, medium: 0.3, large: 0.5 };
// A wall has no height to judge distance by; where it meets the floor does,
// given the height of the camera above the floor: glasses on a standing
// adult, unless the page URL says ?eye=1.2 (metres) for someone seated.
const EYE_HEIGHT_M = (() => {
  const value = Number(new URLSearchParams(location.search).get("eye"));
  return value > 0.3 && value < 2.5 ? value : 1.6;
})();
// A box bottom this low is the frame's edge, not the floor line; a floor line
// this close to the horizon is too far away to judge.
const FLOOR_LINE_MAX_Y = 0.97;
const FLOOR_MIN_BELOW_DEG = 3;
const WALL_BAND_M = 0.25;
// Two views line up when at least this many shared things agree on the turn
// between them, to within this spread.
const ALIGN_MIN_MATCHES = 2;
const ALIGN_MAX_SPREAD_DEG = 25;
// The same thing seen twice: same label, about the same direction and distance.
const SAME_THING_DEG = 15;
const SAME_THING_RATIO = 1.3;

const toRad = (deg: number): number => (deg * Math.PI) / 180;
const toDeg = (rad: number): number => (rad * 180) / Math.PI;
const halfWidth = (item: MapItem): number => HALF_WIDTH_M[item.scale ?? "large"] ?? 0.4;
const isWall = (item: MapItem): boolean => item.label === "wall";
const hasDistance = (item: MapItem): item is MapItem & { distance_m: number } =>
  item.distance_m !== null && item.distance_m !== undefined;

/** The directions a wall's edges were seen at and, from where it meets the
 * floor, how far it is; null when the floor line is out of the frame. */
export function wallGeometry(
  item: MapItem,
  aspect: number | null
): { span: [number, number]; distance: number | null } {
  const tanHalfH = Math.tan(toRad(HALF_FOV_DEG));
  const azimuthOf = (x: number): number => toDeg(Math.atan((x - 0.5) * 2 * tanHalfH));
  if (!item.box) {
    return { span: [item.azimuth_deg - 10, item.azimuth_deg + 10], distance: item.distance_m ?? null };
  }
  const [x1, , x2, y2] = item.box;
  const span: [number, number] = [azimuthOf(x1), azimuthOf(x2)];
  if (!aspect || y2 > FLOOR_LINE_MAX_Y) return { span, distance: null };
  const tanHalfV = tanHalfH * aspect;
  const below = Math.atan((y2 - 0.5) * 2 * tanHalfV); // below the horizon, at the floor line
  if (below < toRad(FLOOR_MIN_BELOW_DEG)) return { span, distance: null };
  const distance = Math.min(RANGE_M, Math.round((EYE_HEIGHT_M / Math.tan(below)) * 10) / 10);
  return { span, distance };
}

/** Walls get their span and floor-line distance; everything else passes through. */
export function enrich(items: MapItem[], aspect: number | null): MapItem[] {
  return items.map((item) => {
    if (!isWall(item)) return { ...item };
    const geometry = wallGeometry(item, aspect);
    return { ...item, span: geometry.span, distance_m: geometry.distance, estimated: geometry.distance !== null };
  });
}

const spanCovers = (wall: MapItem, bearing: number, slack = 1): boolean => {
  if (!wall.span) return false;
  const [left, right] = [Math.min(...wall.span), Math.max(...wall.span)];
  return bearing >= left - slack && bearing <= right + slack;
};

/** What the floor is at a point (x to the right, y ahead, in metres). */
export function classifyCell(x: number, y: number, obstacles: MapItem[], walls: MapItem[] = []): Cell {
  const range = Math.hypot(x, y);
  const bearing = toDeg(Math.atan2(x, y));
  if (y < 0 || range > RANGE_M || Math.abs(bearing) > HALF_FOV_DEG) return "unknown";
  let boundary: { distance: number; half: number } | null = null;
  for (const item of obstacles) {
    if (!item.obstacle || !hasDistance(item) || isWall(item)) continue;
    const half = halfWidth(item);
    const halfAngle = toDeg(Math.atan2(half, item.distance_m)) + 2;
    if (Math.abs(bearing - item.azimuth_deg) > halfAngle) continue;
    if (!boundary || item.distance_m < boundary.distance) boundary = { distance: item.distance_m, half };
  }
  for (const wall of walls) {
    if (!hasDistance(wall) || !spanCovers(wall, bearing)) continue;
    if (!boundary || wall.distance_m < boundary.distance) boundary = { distance: wall.distance_m, half: WALL_BAND_M };
  }
  if (!boundary) return "clear";
  if (range < boundary.distance - boundary.half) return "clear";
  if (range <= boundary.distance + boundary.half) return "blocked";
  return "unknown";
}

/** The walkway rule the spoken scan uses: something to walk into, within
 * three metres and half a metre of the centre line, blocks it; so does a
 * wall whose floor line is in view and whose span crosses the strip. */
export function walkway(items: MapItem[]): { clear: boolean; blocker: MapItem | null } {
  const blockers = items.filter(
    (item) =>
      item.obstacle &&
      !isWall(item) &&
      hasDistance(item) &&
      item.distance_m <= WALK_DISTANCE_M &&
      Math.abs(item.distance_m * Math.sin(toRad(item.azimuth_deg))) <= WALK_WIDTH_M / 2
  );
  for (const wall of items.filter(isWall)) {
    if (!wall.span || !hasDistance(wall) || wall.distance_m > WALK_DISTANCE_M) continue;
    const halfStrip = toDeg(Math.atan2(WALK_WIDTH_M / 2, wall.distance_m));
    const [left, right] = [Math.min(...wall.span), Math.max(...wall.span)];
    if (right >= -halfStrip && left <= halfStrip) blockers.push(wall);
  }
  if (blockers.length === 0) return { clear: true, blocker: null };
  blockers.sort((a, b) => (a.distance_m ?? 0) - (b.distance_m ?? 0));
  return { clear: false, blocker: blockers[0] };
}

const median = (values: number[]): number => {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

function sameThing(a: MapItem, b: MapItem): boolean {
  if (a.label !== b.label) return false;
  if (Math.abs(a.azimuth_deg - b.azimuth_deg) > SAME_THING_DEG) return false;
  if (!hasDistance(a) || !hasDistance(b)) return true;
  const [near, far] = [Math.min(a.distance_m, b.distance_m), Math.max(a.distance_m, b.distance_m)];
  return far / Math.max(near, 0.1) <= SAME_THING_RATIO;
}

/**
 * One layout from a place's views. The newest view is the base; each older
 * view is turned by the median difference in direction of the things it
 * shares with the base, and added only when enough things agree on that
 * turn. Things already drawn are not drawn twice.
 */
export function mergeViews(scenes: SceneLayout[]): { layout: MapItem[]; views: number; aligned: number } {
  const withLayout = scenes.filter((scene) => (scene.layout ?? []).length > 0);
  if (withLayout.length === 0) return { layout: [], views: 0, aligned: 0 };
  const ordered = [...withLayout].sort((a, b) => b.at - a.at);
  const merged: MapItem[] = ordered[0].layout!.map((item) => ({ ...item }));
  let aligned = 1;
  for (const scene of ordered.slice(1)) {
    const view = scene.layout!;
    const turns: number[] = [];
    for (const item of view) {
      for (const known of merged) {
        if (item.label !== known.label || isWall(item)) continue;
        if (hasDistance(item) && hasDistance(known)) {
          const [near, far] = [Math.min(item.distance_m, known.distance_m), Math.max(item.distance_m, known.distance_m)];
          if (far / Math.max(near, 0.1) > 1.5) continue;
        }
        turns.push(known.azimuth_deg - item.azimuth_deg);
      }
    }
    if (turns.length < ALIGN_MIN_MATCHES) continue;
    const spread = Math.max(...turns) - Math.min(...turns);
    if (spread > ALIGN_MAX_SPREAD_DEG) continue;
    const turn = median(turns);
    aligned += 1;
    for (const item of view) {
      const placed: MapItem = { ...item, azimuth_deg: item.azimuth_deg + turn };
      if (item.span) placed.span = [item.span[0] + turn, item.span[1] + turn];
      if (!merged.some((known) => sameThing(known, placed))) merged.push(placed);
    }
  }
  return { layout: merged, views: withLayout.length, aligned };
}

const el = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

export function initBlueprint(options: { onOpen?: () => void } = {}) {
  const handle = el<HTMLButtonElement>("blueprint-handle");
  const panel = el<HTMLElement>("blueprint-panel");
  const canvas = el<HTMLCanvasElement>("blueprint-canvas");
  const pick = el<HTMLSelectElement>("blueprint-pick");
  const note = el<HTMLDivElement>("blueprint-note");
  const closeButton = el<HTMLButtonElement>("blueprint-close");

  let open = false;
  let latest: MapItem[] | null = null;
  let latestAspect: number | null = null;
  let latestAt: Date | null = null;
  let latestPlace: PlaceEvent | null = null;
  let memory: Memory = { places: [], current: null };

  const meters = (distance: number): string =>
    `${distance < 3 ? distance.toFixed(1) : distance.toFixed(0)} m`;

  function setOpen(next: boolean): void {
    open = next;
    panel.dataset.open = String(next);
    handle.setAttribute("aria-expanded", String(next));
    if (next) {
      options.onOpen?.();
      render();
    }
  }

  function fillPick(): void {
    const chosen = pick.value;
    pick.replaceChildren();
    const now = document.createElement("option");
    now.value = "";
    now.textContent = "Latest scan";
    pick.append(now);
    for (const place of memory.places) {
      const option = document.createElement("option");
      option.value = place.id;
      const here = memory.current?.fresh && memory.current.place_id === place.id ? " (you are here)" : "";
      option.textContent = `${place.name}${here}`;
      pick.append(option);
    }
    pick.value = memory.places.some((place) => place.id === chosen) ? chosen : "";
  }

  type Label = { x: number; y: number; text: string; width: number; color: string; alpha: number };

  /** Draw a layout from above. */
  function draw(items: MapItem[], title: string, footer: string): void {
    const width = Math.max(240, Math.floor(panel.clientWidth - 32));
    const height = Math.min(440, Math.round(width * 0.62));
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0b0b0e";
    ctx.fillRect(0, 0, width, height);

    const cx = width / 2;
    const cy = height - 26;
    const scale = (height - 54) / RANGE_M; // pixels per metre
    const px = (x: number, y: number): [number, number] => [cx + x * scale, cy - y * scale];
    const walls = items.filter(isWall);
    const obstacles = items.filter((item) => item.obstacle && !isWall(item));

    // The floor: what the scan could tell about each half-metre cell. A
    // scan that saw nothing at all tells nothing, so the floor stays unknown.
    if (items.length > 0) {
      for (let gx = -RANGE_M; gx < RANGE_M; gx += CELL_M) {
        for (let gy = 0; gy < RANGE_M; gy += CELL_M) {
          const cell = classifyCell(gx + CELL_M / 2, gy + CELL_M / 2, obstacles, walls);
          if (cell === "unknown") continue;
          ctx.fillStyle = cell === "clear" ? "rgba(53, 208, 127, 0.16)" : "rgba(255, 75, 62, 0.38)";
          const [x0, y0] = px(gx, gy + CELL_M);
          ctx.fillRect(x0 + 0.5, y0 + 0.5, CELL_M * scale - 1, CELL_M * scale - 1);
        }
      }
    }

    // Rings every metre, and the edges of what the camera sees.
    ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 6]);
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillStyle = "rgba(255, 255, 255, 0.45)";
    for (let ring = 1; ring <= RANGE_M; ring++) {
      ctx.beginPath();
      ctx.arc(cx, cy, ring * scale, Math.PI, 2 * Math.PI);
      ctx.stroke();
      ctx.fillText(`${ring} m`, cx + 4, cy - ring * scale - 3);
    }
    ctx.setLineDash([]);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.22)";
    for (const sign of [-1, 1]) {
      const [x, y] = px(sign * RANGE_M * Math.sin(toRad(HALF_FOV_DEG)), RANGE_M * Math.cos(toRad(HALF_FOV_DEG)));
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(x, y);
      ctx.stroke();
    }

    const labels: Label[] = [];

    // Walls: a line across the directions each was seen in, at the distance
    // its floor line gives, or dashed at the edge of the map when the floor
    // line was out of the frame.
    for (const wall of walls) {
      const [a, b] = wall.span ?? [wall.azimuth_deg - 10, wall.azimuth_deg + 10];
      const known = hasDistance(wall);
      const radiusM = known ? Math.min(wall.distance_m as number, RANGE_M) : RANGE_M - 0.15;
      ctx.globalAlpha = wall.frames === 1 ? 0.45 : 1;
      ctx.strokeStyle = "#9db4ff";
      ctx.lineWidth = known ? 5 : 3;
      ctx.setLineDash(known ? [] : [8, 6]);
      ctx.beginPath();
      ctx.arc(cx, cy, radiusM * scale, toRad(Math.min(a, b) - 90), toRad(Math.max(a, b) - 90));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      const mid = (a + b) / 2;
      const [lx, ly] = px(radiusM * Math.sin(toRad(mid)), radiusM * Math.cos(toRad(mid)));
      const text = known ? `wall ~${meters(wall.distance_m as number)}` : "wall ?";
      ctx.font = "12px system-ui, sans-serif";
      const textWidth = ctx.measureText(text).width;
      labels.push({ x: lx - textWidth / 2, y: ly - 12, text, width: textWidth, color: "#c3d0ff", alpha: wall.frames === 1 ? 0.55 : 1 });
    }

    // The walkway strip, and whether it is clear.
    const way = walkway(items);
    ctx.strokeStyle = way.clear ? "rgba(53, 208, 127, 0.9)" : "rgba(255, 75, 62, 0.95)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    const [wx, wy] = px(-WALK_WIDTH_M / 2, WALK_DISTANCE_M);
    ctx.strokeRect(wx, wy, WALK_WIDTH_M * scale, WALK_DISTANCE_M * scale);
    ctx.setLineDash([]);
    ctx.fillStyle = way.clear ? "rgba(53, 208, 127, 0.95)" : "rgba(255, 75, 62, 0.95)";
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.fillText(way.clear ? "walkway: clear" : "walkway: blocked", wx + 4, wy - 4);

    // The things seen, red when they obstruct; a glimpse is faded, and a
    // thing with no distance sits on the far ring with a question mark.
    // Labels go on after every dot, each nudged down until it sits clear of
    // the ones before it, so two things side by side both stay readable.
    ctx.font = "12px system-ui, sans-serif";
    for (const item of items) {
      if (isWall(item)) continue;
      const glimpse = item.frames === 1;
      const known = hasDistance(item);
      const distance = known ? Math.min(item.distance_m as number, RANGE_M) : RANGE_M;
      const [x, y] = px(distance * Math.sin(toRad(item.azimuth_deg)), distance * Math.cos(toRad(item.azimuth_deg)));
      const radius = item.scale === "small" ? 4 : item.scale === "medium" ? 6 : 8;
      ctx.globalAlpha = glimpse ? 0.45 : 1;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, 2 * Math.PI);
      if (known) {
        ctx.fillStyle = item.obstacle ? "#ff4b3e" : "#ffffff";
        ctx.fill();
      } else {
        ctx.strokeStyle = item.obstacle ? "#ff4b3e" : "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      const text = known ? `${item.label} ${meters(item.distance_m as number)}` : `${item.label} ?`;
      const textWidth = ctx.measureText(text).width;
      const labelX = x + radius + 3 + textWidth > width - 4 ? x - radius - 3 - textWidth : x + radius + 3;
      labels.push({ x: labelX, y, text, width: textWidth, color: item.obstacle ? "#ff8a80" : "#f2f2f2", alpha: glimpse ? 0.55 : 1 });
    }
    const placed: Array<{ x: number; y: number; width: number }> = [];
    const collides = (x: number, y: number, w: number): boolean =>
      placed.some((p) => x < p.x + p.width + 4 && p.x < x + w + 4 && Math.abs(y - p.y) < 15);
    for (const label of labels) {
      let y = label.y;
      for (let tries = 0; tries < 4 && collides(label.x, y, label.width); tries++) y += 15;
      placed.push({ x: label.x, y, width: label.width });
      ctx.globalAlpha = label.alpha;
      ctx.fillStyle = "rgba(11, 11, 14, 0.8)";
      ctx.fillRect(label.x - 2, y - 8, label.width + 4, 15);
      ctx.fillStyle = label.color;
      ctx.fillText(label.text, label.x, y + 4);
      ctx.globalAlpha = 1;
    }

    // You.
    ctx.fillStyle = "#ffd400";
    ctx.beginPath();
    ctx.moveTo(cx, cy - 9);
    ctx.lineTo(cx - 7, cy + 6);
    ctx.lineTo(cx + 7, cy + 6);
    ctx.closePath();
    ctx.fill();
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.fillText("you", cx + 11, cy + 4);

    ctx.font = "600 13px system-ui, sans-serif";
    ctx.fillStyle = "#f2f2f2";
    ctx.fillText(title, 10, 18);
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillStyle = "rgba(255, 255, 255, 0.6)";
    ctx.fillText(footer, 10, 34);
  }

  function describeWalkway(items: MapItem[]): string {
    const way = walkway(items);
    if (!way.clear) {
      const blocker = way.blocker!;
      const what = isWall(blocker) ? "a wall" : blocker.label;
      const about = isWall(blocker) ? "roughly" : "about";
      return `Walkway ahead: blocked by ${what} ${about} ${meters(blocker.distance_m ?? 0)} ahead.`;
    }
    const wallAhead = items.find((item) => isWall(item) && spanCovers(item, 0, 3));
    if (wallAhead && !hasDistance(wallAhead)) {
      return "Walkway ahead: clear as far as the scan can tell, and it leads to a wall; how far is not in view.";
    }
    if (wallAhead) {
      return `Walkway ahead: clear as far as the scan can tell, and it leads to a wall roughly ${meters(wallAhead.distance_m as number)} ahead.`;
    }
    return "Walkway ahead: clear, as far as the scan can tell.";
  }

  function render(): void {
    fillPick();
    if (!open) return;
    const placeId = pick.value;
    if (placeId) {
      const place = memory.places.find((p) => p.id === placeId);
      if (!place) return;
      const merged = mergeViews(
        place.scenes.map((scene) => ({ ...scene, layout: enrich(scene.layout ?? [], scene.aspect ?? null) }))
      );
      const footer =
        merged.views === 0
          ? "no views with a layout yet"
          : `${merged.aligned} of ${merged.views} ${merged.views === 1 ? "view" : "views"} lined up, ${merged.layout.length} things`;
      draw(merged.layout, `Blueprint of ${place.name}`, footer);
      note.textContent =
        merged.views === 0
          ? `${place.name} has no saved layout yet; scan while you are there.`
          : `${place.name}, from its saved views. ${describeWalkway(merged.layout)}`;
      return;
    }
    if (!latest) {
      draw([], "Latest scan", "no scan yet");
      note.textContent = "Scan to draw the first blueprint.";
      return;
    }
    const items = enrich(latest, latestAspect);
    const when = latestAt ? latestAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }) : "";
    const where =
      latestPlace?.kind === "recognized" && latestPlace.place
        ? `in ${latestPlace.place.name}`
        : latestPlace?.kind === "new" && latestPlace.place
          ? `new place ${latestPlace.place.name}`
          : latestPlace?.kind === "unsure" && latestPlace.candidate
            ? `might be ${latestPlace.candidate.name}`
            : "";
    draw(items, `Latest scan${where ? `, ${where}` : ""}`, `${when} · ${items.length} things`);
    note.textContent = describeWalkway(items);
  }

  handle.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(!open);
  });
  closeButton.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(false);
  });
  panel.addEventListener("click", (e) => e.stopPropagation());
  pick.addEventListener("change", () => render());
  window.addEventListener("resize", () => {
    if (open) render();
  });

  return {
    /** Show the pill once the app is running. */
    reveal(): void {
      handle.hidden = false;
    },
    toggle(): void {
      setOpen(!open);
    },
    close(): void {
      setOpen(false);
    },
    get isOpen(): boolean {
      return open;
    },
    /** What the latest scan saw, with direction and distance, and the
     * frame's size so a wall's floor line can be turned into a distance. */
    onInventory(items: MapItem[], frameSize?: [number, number]): void {
      latest = items.map((item) => ({ ...item }));
      latestAspect = frameSize && frameSize[0] > 0 ? frameSize[1] / frameSize[0] : latestAspect;
      latestAt = new Date();
      latestPlace = null;
      handle.textContent = "Blueprint";
      handle.dataset.tone = "new";
      window.setTimeout(() => {
        delete handle.dataset.tone;
      }, 2500);
      render();
    },
    /** What the memory made of that scan, for the title. */
    onPlaceEvent(event: PlaceEvent): void {
      latestPlace = event;
      render();
    },
    /** The remembered places and their views, for the picker. */
    onMemory(next: Memory): void {
      memory = next;
      render();
    },
  };
}
