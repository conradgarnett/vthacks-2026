import {
  AgentRequestSchema,
  canonicalJson,
  iso,
  type AgentRequest,
  type Clock,
  type SensoryProfile,
} from '@sense/protocol';

export interface DisclosureEntry {
  id: string;
  timestamp: string;
  /** Who it was sent to. */
  to: string;
  /** Ephemeral session id, never a user identity. */
  sessionId: string;
  messageType: AgentRequest['type'];
  capability?: string;
  scope?: string[];
  /** Exactly what left the device (canonical JSON of the request). */
  sent: string;
  /** Always false: the gate refuses to send anything containing profile data. */
  profileDataSent: false;
}

export class OutboundBlocked extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'OutboundBlocked';
  }
}

/** Values that identify the user's profile, derived locally. Never sent to a remote agent. */
export function profileTokens(profile: SensoryProfile): string[] {
  const tokens = new Set<string>();
  for (const a of profile.allergens) tokens.add(a.toLowerCase());
  tokens.add(profile.name.toLowerCase());
  tokens.add(profile.id.toLowerCase());
  if (profile.personaId) tokens.add(profile.personaId.toLowerCase());
  return [...tokens].filter((t) => t.length >= 3);
}

/** Generic profile vocabulary is blocked as JSON keys only (it also appears in capability ids like "menu-allergens"). */
const PROFILE_KEYS = /"(profile|persona|allergens?|disabilit(?:y|ies)|impairments?|sensory)"\s*:/i;

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Whole-word scan (words delimited by anything that is not a letter or digit). */
export function leaksProfile(serialized: string, profile: SensoryProfile): string | undefined {
  const key = PROFILE_KEYS.exec(serialized);
  if (key) return key[1];
  for (const t of profileTokens(profile)) {
    if (new RegExp(`(^|[^a-z0-9])${esc(t)}([^a-z0-9]|$)`, 'i').test(serialized)) return t;
  }
  return undefined;
}

/**
 * Every outbound request passes through here: it must match the strict request schema (no extra
 * fields) and must not contain anything derived from the user's profile. Each send is logged so the
 * user can see exactly what was disclosed to whom.
 */
export class DisclosureLog {
  private readonly log: DisclosureEntry[] = [];
  private n = 0;

  constructor(
    private readonly clock: Clock,
    private readonly getProfile: () => SensoryProfile,
  ) {}

  /** Validate and record an outbound request. Throws OutboundBlocked instead of sending. */
  approve(to: string, msg: AgentRequest): DisclosureEntry {
    const parsed = AgentRequestSchema.safeParse(msg);
    if (!parsed.success)
      throw new OutboundBlocked('outbound message does not match the request schema');
    const sent = canonicalJson(parsed.data);
    const leaked = leaksProfile(sent, this.getProfile());
    if (leaked)
      throw new OutboundBlocked(`outbound message would disclose profile data ("${leaked}")`);
    const entry: DisclosureEntry = {
      id: `d-${++this.n}`,
      timestamp: iso(this.clock),
      to,
      sessionId: parsed.data.sessionId,
      messageType: parsed.data.type,
      ...('capability' in parsed.data ? { capability: parsed.data.capability } : {}),
      ...('scope' in parsed.data ? { scope: parsed.data.scope } : {}),
      sent,
      profileDataSent: false,
    };
    this.log.push(entry);
    return entry;
  }

  entries(): DisclosureEntry[] {
    return [...this.log];
  }
}
