import { canonicalJson } from '@sense/protocol';

/**
 * SENSE's policy. It is deep-frozen at creation. No code path lets remote content write to it, and
 * the invariants suite hashes it before and after hostile input to prove that.
 */
export interface Policy {
  readonly remoteContentIsData: true;
  readonly profileNeverSent: true;
  readonly neverAssertSafety: true;
  readonly rejectedNeverRouted: true;
  readonly maxFieldLength: number;
  /** Tools the broker may use on remote agents. Remote content cannot add to this. */
  readonly allowedTools: readonly string[];
  /** Scopes the broker may ever request, per capability (minimum necessary). */
  readonly scopes: Readonly<Record<string, readonly string[]>>;
}

function deepFreeze<T>(o: T): T {
  if (o && typeof o === 'object') {
    Object.freeze(o);
    for (const v of Object.values(o as Record<string, unknown>)) deepFreeze(v);
  }
  return o;
}

export function createPolicy(): Policy {
  return deepFreeze({
    remoteContentIsData: true,
    profileNeverSent: true,
    neverAssertSafety: true,
    rejectedNeverRouted: true,
    maxFieldLength: 300,
    allowedTools: ['hello', 'capability_query', 'subscribe', 'unsubscribe'],
    scopes: {
      'indoor-map': ['indoor-map:read'],
      'alarm-feed': ['alarm-feed:read'],
      'air-quality': ['air-quality:read'],
      'menu-allergens': ['menu-allergens:read'],
      arrivals: ['arrivals:read'],
      'accessibility-features': ['accessibility-features:read'],
      'device-control': ['lift:call'],
    },
  } as const);
}

export function policyHash(p: Policy): string {
  return canonicalJson(p);
}
