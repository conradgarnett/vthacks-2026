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
};
type SceneLayout = { id: string; at: number; layout?: MapItem[] };
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

/** What the floor is at a point (x to the right, y ahead, in metres). */
export function classifyCell(x: number, y: number, obstacles: MapItem[]): Cell {
  const range = Math.hypot(x, y);
  const bearing = toDeg(Math.atan2(x, y));
  if (y < 0 || range > RANGE_M || Math.abs(bearing) > HALF_FOV_DEG) return "unknown";
  let nearest: { distance: number; half: number } | null = null;
  for (const item of obstacles) {
    if (!item.obstacle || item.distance_m === null || item.distance_m === undefined) continue;
    const half = halfWidth(item);
    const halfAngle = toDeg(Math.atan2(half, item.distance_m)) + 2;
    if (Math.abs(bearing - item.azimuth_deg) > halfAngle) continue;
    if (!nearest || item.distance_m < nearest.distance) nearest = { distance: item.distance_m, half };
  }
  if (!nearest) return "clear";
  if (range < nearest.distance - nearest.half) return "clear";
  if (range <= nearest.distance + nearest.half) return "blocked";
  return "unknown";
}

/** The walkway rule the spoken scan uses: something to walk into, within
 * three metres and half a metre of the centre line, blocks it. */
export function walkway(items: MapItem[]): { clear: boolean; blocker: MapItem | null } {
  const blockers = items.filter(
    (item) =>
      item.obstacle &&
      item.distance_m !== null &&
      item.distance_m !== undefined &&
      item.distance_m <= WALK_DISTANCE_M &&
      Math.abs(item.distance_m * Math.sin(toRad(item.azimuth_deg))) <= WALK_WIDTH_M / 2
  );
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
  if (a.distance_m === null || b.distance_m === null || a.distance_m === undefined || b.distance_m === undefined) {
    return true;
  }
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
        if (item.label !== known.label) continue;
        if (
          item.distance_m !== null && item.distance_m !== undefined &&
          known.distance_m !== null && known.distance_m !== undefined
        ) {
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
      const placed = { ...item, azimuth_deg: item.azimuth_deg + turn };
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
  let latestAt: Date | null = null;
  let latestPlace: PlaceEvent | null = null;
  let memory: Memory = { places: [], current: null };

  const meters = (distance: number): string => `${distance < 1 ? distance.toFixed(1) : distance.toFixed(distance < 3 ? 1 : 0)} m`;

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
    const obstacles = items.filter((item) => item.obstacle);

    // The floor: what the scan could tell about each half-metre cell. A
    // scan that saw nothing at all tells nothing, so the floor stays unknown.
    if (items.length > 0) {
      for (let gx = -RANGE_M; gx < RANGE_M; gx += CELL_M) {
        for (let gy = 0; gy < RANGE_M; gy += CELL_M) {
          const cell = classifyCell(gx + CELL_M / 2, gy + CELL_M / 2, obstacles);
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
    const labels: Array<{ x: number; y: number; text: string; width: number; obstacle: boolean; alpha: number }> = [];
    for (const item of items) {
      const glimpse = item.frames === 1;
      const known = item.distance_m !== null && item.distance_m !== undefined;
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
      labels.push({ x: labelX, y, text, width: textWidth, obstacle: Boolean(item.obstacle), alpha: glimpse ? 0.55 : 1 });
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
      ctx.fillStyle = label.obstacle ? "#ff8a80" : "#f2f2f2";
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
    if (way.clear) return "Walkway ahead: clear, as far as the scan can tell.";
    const blocker = way.blocker!;
    return `Walkway ahead: blocked by ${blocker.label} about ${meters(blocker.distance_m ?? 0)} ahead.`;
  }

  function render(): void {
    fillPick();
    if (!open) return;
    const placeId = pick.value;
    if (placeId) {
      const place = memory.places.find((p) => p.id === placeId);
      if (!place) return;
      const merged = mergeViews(place.scenes);
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
    const when = latestAt ? latestAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }) : "";
    const where =
      latestPlace?.kind === "recognized" && latestPlace.place
        ? `in ${latestPlace.place.name}`
        : latestPlace?.kind === "new" && latestPlace.place
          ? `new place ${latestPlace.place.name}`
          : latestPlace?.kind === "unsure" && latestPlace.candidate
            ? `might be ${latestPlace.candidate.name}`
            : "";
    draw(latest, `Latest scan${where ? `, ${where}` : ""}`, `${when} · ${latest.length} things`);
    note.textContent = describeWalkway(latest);
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
    /** What the latest scan saw, with direction and distance. */
    onInventory(items: MapItem[]): void {
      latest = items.map((item) => ({ ...item }));
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
