import { PerceptSchema, type Percept } from '@sense/protocol';

/**
 * Words and phrases SENSE itself must never use to reassure. The guard runs on every percept the
 * broker emits, so this holds for verified sources too: SENSE may say "no hazard reported by
 * verified sources", never "the air is safe".
 */
const ASSURANCE =
  /\b(safe|safely|safety-approved|all[- ]clear|harmless|no danger|nothing to worry about|risk[- ]free|fine to eat|ok to eat|okay to eat)\b/i;

export class SafetyViolation extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SafetyViolation';
  }
}

/** Text that SENSE generates. Throws if it would reassure the user. */
export function assertNoAssurance(text: string, where = 'text'): void {
  const m = ASSURANCE.exec(text);
  if (m) throw new SafetyViolation(`${where} contains assurance wording "${m[0]}"`);
}

export function containsAssurance(text: string): boolean {
  return ASSURANCE.test(text);
}

const ASSURANCE_GLOBAL = new RegExp(ASSURANCE.source, 'gi');

/** Replace assurance wording (for example inside a remote label such as "Safe room"). */
export function redactAssurance(text: string): string {
  return text.replace(ASSURANCE_GLOBAL, '[removed]');
}

/**
 * Final gate for any percept: schema-valid, never REJECTED, no assurance wording, and safety
 * percepts state their provenance in the primary message (enforced by the schema).
 * With `redact`, assurance wording that came from remote text is removed instead of throwing;
 * `redacted` tells the caller so it can log a security event.
 */
export function guardPercept(p: Percept): Percept;
export function guardPercept(p: Percept, opts: { redact: true }): { percept: Percept; redacted: boolean };
export function guardPercept(p: Percept, opts?: { redact: true }): Percept | { percept: Percept; redacted: boolean } {
  let candidate = p;
  let redacted = false;
  if (opts?.redact && (containsAssurance(p.short) || (p.long !== undefined && containsAssurance(p.long)))) {
    candidate = {
      ...p,
      short: redactAssurance(p.short),
      ...(p.long !== undefined ? { long: redactAssurance(p.long) } : {}),
    };
    redacted = true;
  }
  const parsed = PerceptSchema.parse(candidate);
  if (parsed.provenance.tier === 'REJECTED') {
    throw new SafetyViolation('REJECTED content is never routed as information');
  }
  assertNoAssurance(parsed.short, 'short');
  if (parsed.long) assertNoAssurance(parsed.long, 'long');
  return opts?.redact ? { percept: parsed, redacted } : parsed;
}

/** Hedged wording helpers so inference-only output never sounds like a guarantee. */
export function notDetectedUnverified(what: string): string {
  return `${what} not detected, unverified`;
}
