import type { SceneGraph } from '@sense/providers';

export type MemoryKind = 'object' | 'sign' | 'hazard';

export interface MemoryItem {
  key: string;
  type: MemoryKind;
  label: string;
  /** Finer kind for objects (door, exit, obstacle, ...). */
  kind: string;
  /** World bearing (clockwise from north-ish reference), so it survives the user turning. */
  absBearingDeg: number;
  distance: 'near' | 'mid' | 'far';
  distanceM?: number;
  confidence: number;
  severity?: 'low' | 'medium' | 'high';
  firstSeenMs: number;
  lastSeenMs: number;
  seenCount: number;
}

const norm = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, '')
    .trim();
const diff = (a: number, b: number) => {
  const d = Math.abs(((((a - b) % 360) + 540) % 360) - 180);
  return d;
};

/**
 * Persistent scene memory across frames. Items are remembered for a while after they leave the
 * frame, with confidence decaying, and bearings are stored against the heading they were seen at
 * so "where is the door?" stays right after the user turns.
 */
export class SceneMemory {
  private readonly items = new Map<string, MemoryItem>();
  summary = '';
  peopleCount = 0;
  lighting: SceneGraph['lighting'] = 'normal';
  lastFrameMs: number | undefined;

  constructor(
    /** Items older than this are forgotten. */
    private readonly ttlMs = 5 * 60_000,
    /** Confidence halves roughly every this many ms without being seen again. */
    private readonly halfLifeMs = 60_000,
  ) {}

  update(graph: SceneGraph, nowMs: number, headingDeg: number): void {
    this.summary = graph.summary;
    this.peopleCount = graph.peopleCount;
    this.lighting = graph.lighting;
    this.lastFrameMs = nowMs;
    const abs = (rel: number) => (((headingDeg + rel) % 360) + 360) % 360;
    for (const o of graph.objects) {
      this.merge('object', o.label, o.kind, abs(o.bearingDeg), o.distance, o.distanceM, o.confidence, undefined, nowMs);
    }
    for (const s of graph.signs)
      this.merge('sign', s.text, 'sign', abs(s.bearingDeg), s.distance, undefined, s.confidence, undefined, nowMs);
    for (const h of graph.hazards)
      this.merge('hazard', h.label, 'hazard', abs(h.bearingDeg), h.distance, undefined, h.confidence, h.severity, nowMs);
    this.expire(nowMs);
  }

  private merge(
    type: MemoryKind,
    label: string,
    kind: string,
    absBearingDeg: number,
    distance: MemoryItem['distance'],
    distanceM: number | undefined,
    confidence: number,
    severity: MemoryItem['severity'],
    nowMs: number,
  ): void {
    const l = norm(label);
    for (const it of this.items.values()) {
      if (it.type === type && norm(it.label) === l && diff(it.absBearingDeg, absBearingDeg) <= 30) {
        it.absBearingDeg = absBearingDeg;
        it.distance = distance;
        if (distanceM !== undefined) it.distanceM = distanceM;
        it.confidence = Math.max(confidence, it.confidence * 0.9);
        if (severity) it.severity = severity;
        it.lastSeenMs = nowMs;
        it.seenCount += 1;
        return;
      }
    }
    const key = `${type}:${l}:${Math.round(absBearingDeg / 30)}:${this.items.size}`;
    this.items.set(key, {
      key,
      type,
      label,
      kind,
      absBearingDeg,
      distance,
      ...(distanceM !== undefined ? { distanceM } : {}),
      confidence,
      ...(severity ? { severity } : {}),
      firstSeenMs: nowMs,
      lastSeenMs: nowMs,
      seenCount: 1,
    });
  }

  expire(nowMs: number): void {
    for (const [k, it] of this.items) if (nowMs - it.lastSeenMs > this.ttlMs) this.items.delete(k);
  }

  /** Items with confidence decayed for how long ago they were last seen, plus a bearing relative to `headingDeg`. */
  recall(nowMs: number, headingDeg: number): (MemoryItem & { relBearingDeg: number; ageMs: number; remembered: boolean })[] {
    return [...this.items.values()]
      .filter((it) => nowMs - it.lastSeenMs <= this.ttlMs)
      .map((it) => {
        const ageMs = Math.max(0, nowMs - it.lastSeenMs);
        const remembered = this.lastFrameMs !== undefined && it.lastSeenMs < this.lastFrameMs;
        return {
          ...it,
          confidence: Math.round(it.confidence * 0.5 ** (ageMs / this.halfLifeMs) * 100) / 100,
          relBearingDeg: (((it.absBearingDeg - headingDeg) % 360) + 360) % 360,
          ageMs,
          remembered,
        };
      });
  }

  get size(): number {
    return this.items.size;
  }

  clear(): void {
    this.items.clear();
    this.summary = '';
    this.peopleCount = 0;
    this.lastFrameMs = undefined;
  }
}
