import type { Percept, SecurityEvent } from './percept';
import type { SensoryProfile } from './profile';

/**
 * Types shared by the local SENSE server and the web app. The web app never imports Node-only
 * packages (identity, core); it receives these plain JSON shapes.
 */

export interface VerificationStepDto {
  step: number;
  name: string;
  status: 'pass' | 'fail' | 'unavailable' | 'skipped';
  evidence: string[];
}

export interface VerificationDto {
  fqdn: string;
  displayName: string;
  outcome: 'VERIFIED' | 'UNVERIFIED' | 'REJECTED';
  steps: VerificationStepDto[];
  failingStep?: number;
  failingStepName?: string;
  unavailableStep?: number;
  verifiedAt: string;
  ansMode: 'simulated' | 'live';
  sessionId: string;
  connectedAt: string;
  capabilities: string[];
  subscriptions: string[];
  simulated: boolean;
}

export interface DisclosureDto {
  id: string;
  timestamp: string;
  to: string;
  sessionId: string;
  messageType: string;
  capability?: string;
  scope?: string[];
  sent: string;
  profileDataSent: false;
}

export interface ModeDto {
  /** Shown as a persistent badge whenever the world-sim is the source. */
  world: 'SIMULATED WORLD';
  ans: 'ANS-modeled (simulated)' | 'live ANS (not configured)';
  ai: 'MOCK AI' | 'ANTHROPIC';
  /** Whether the machine had network at startup. Everything works offline. */
  online: boolean;
}

export interface ActionDto {
  id: string;
  timestamp: string;
  kind: string;
  /** How the action was performed: "pointer", "keyboard" or "intent:<driver>". */
  via: string;
  detail: string;
}

export interface SoundEventDto {
  id: string;
  timestamp: string;
  label: string;
  confidence: number;
  bearingDeg?: number;
  directionKnown: boolean;
  provider: string;
}

export interface ScentStatusDto {
  level: 0 | 1 | 2 | 3 | 4;
  summary: string;
  tier: 'VERIFIED' | 'INFERRED' | 'UNVERIFIED';
  rules: string[];
}

export interface StateSnapshot {
  mode: ModeDto;
  profile: SensoryProfile;
  percepts: Percept[];
  acknowledged: string[];
  security: SecurityEvent[];
  verifications: VerificationDto[];
  disclosure: DisclosureDto[];
  actions: ActionDto[];
  user: { place: string; position: { x: number; y: number }; headingDeg: number };
  scent?: ScentStatusDto;
}

export type ServerEvent =
  | { type: 'state'; state: StateSnapshot }
  | { type: 'percept'; percept: Percept }
  | { type: 'security'; event: SecurityEvent }
  | { type: 'verification'; verification: VerificationDto }
  | { type: 'disclosure'; entry: DisclosureDto }
  | { type: 'ack'; perceptId: string; via: string }
  | { type: 'action'; action: ActionDto }
  | { type: 'profile'; profile: SensoryProfile }
  | { type: 'scent'; scent: ScentStatusDto };
