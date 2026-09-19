import type { Spatial } from './percept';

export interface Point2 {
  x: number;
  y: number;
}

/**
 * Local building coordinates: +y is "north", +x is "east", metres. Headings are degrees clockwise
 * from north. Bearings returned here are relative to the user's heading (0 = straight ahead).
 */
export function distanceM(a: Point2, b: Point2): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

export function relativeBearingDeg(from: Point2, to: Point2, headingDeg: number): number {
  const absolute = (Math.atan2(to.x - from.x, to.y - from.y) * 180) / Math.PI; // clockwise from north
  return (((absolute - headingDeg) % 360) + 360) % 360;
}

/** 12 = straight ahead, 3 = right, 6 = behind, 9 = left. */
export function clockPosition(bearingDeg: number): number {
  const h = Math.round((((bearingDeg % 360) + 360) % 360) / 30) % 12;
  return h === 0 ? 12 : h;
}

export function spatialFrom(from: Point2, to: Point2, headingDeg: number): Spatial {
  const bearingDeg = Math.round(relativeBearingDeg(from, to, headingDeg));
  return {
    bearingDeg,
    distanceM: Math.round(distanceM(from, to) * 10) / 10,
    clockPosition: clockPosition(bearingDeg),
  };
}

export function describeDirection(bearingDeg: number): string {
  const b = ((bearingDeg % 360) + 360) % 360;
  if (b < 22.5 || b >= 337.5) return 'ahead';
  if (b < 67.5) return 'ahead right';
  if (b < 112.5) return 'to your right';
  if (b < 157.5) return 'behind right';
  if (b < 202.5) return 'behind you';
  if (b < 247.5) return 'behind left';
  if (b < 292.5) return 'to your left';
  return 'ahead left';
}

export function coarseDistance(m: number): 'near' | 'mid' | 'far' {
  return m < 3 ? 'near' : m < 10 ? 'mid' : 'far';
}
