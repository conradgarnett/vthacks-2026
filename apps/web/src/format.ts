import type { Percept, Tier } from '@sense/protocol';

/** Symbol + word per tier, so meaning never depends on color. */
export const TIER_DISPLAY: Record<Tier, { symbol: string; word: string; help: string }> = {
  VERIFIED: { symbol: '✔', word: 'VERIFIED', help: 'Identity of the source passed every check and the data is fresh.' },
  INFERRED: { symbol: '◐', word: 'INFERRED', help: "SENSE's own camera or model guessed this. Treat as a guess." },
  UNVERIFIED: { symbol: '⚠', word: 'UNVERIFIED', help: 'Source identity checks are incomplete, or the data is out of date.' },
  REJECTED: { symbol: '✖', word: 'REJECTED', help: 'Identity or content checks failed. Never used as information.' },
};

export const URGENCY_LABEL = ['Ambient', 'Low', 'Normal', 'High', 'Life safety'] as const;

export function urgencyLabel(u: number): string {
  return `Urgency ${u}: ${URGENCY_LABEL[Math.min(4, Math.max(0, u))]}`;
}

export function timeLabel(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(11, 19) + ' UTC';
}

export function directionLabel(p: Percept): string | undefined {
  const s = p.spatial;
  if (!s) return undefined;
  const parts: string[] = [];
  if (s.clockPosition) parts.push(`${s.clockPosition} o'clock`);
  if (s.distanceM !== undefined) parts.push(`${Math.round(s.distanceM)} m`);
  parts.push(`bearing ${Math.round(s.bearingDeg)}°`);
  return parts.join(', ');
}

export const STEP_STATUS: Record<'pass' | 'fail' | 'unavailable' | 'skipped', { symbol: string; word: string }> = {
  pass: { symbol: '✔', word: 'Pass' },
  fail: { symbol: '✖', word: 'Fail' },
  unavailable: { symbol: '⚠', word: 'Could not check' },
  skipped: { symbol: '–', word: 'Skipped' },
};

export const SECURITY_LABEL: Record<string, string> = {
  IDENTITY_REJECTED: 'Identity rejected',
  CONTENT_REJECTED: 'Content rejected',
  INJECTION_NEUTRALIZED: 'Instruction-like text removed',
  SIGNATURE_INVALID: 'Bad signature',
  STALE_DOWNGRADED: 'Stale data downgraded',
  SOURCE_OFFLINE: 'Source offline or silent',
  SOURCE_CONFLICT: 'Sources disagree',
  RATE_LIMITED: 'Flood limited',
  SPOOF_SUPPRESSION_BLOCKED: 'Spoofed dismissal blocked',
};
