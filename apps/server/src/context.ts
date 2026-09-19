import type { ActionDto, Clock, DisclosureDto, ModeDto, ServerEvent, StateSnapshot, VerificationDto } from '@sense/protocol';
import { systemClock, iso } from '@sense/protocol';
import { generateKeyPair, createAnsClient, type KeyPair } from '@sense/identity';
import { SenseBroker, type DisclosureEntry, type VerificationView } from '@sense/core';
import { WorldSim } from '@sense/world-sim';
import { ProfileStore } from './profile-store';

export interface ContextOptions {
  clock?: Clock;
  seed?: number;
  /** Directory for the local dev CA (gitignored). Omit for in-memory keys (tests). */
  keyDir?: string;
  env?: Record<string, string | undefined>;
  online?: boolean;
}

export function toVerificationDto(v: VerificationView): VerificationDto {
  return {
    fqdn: v.fqdn,
    displayName: v.displayName,
    outcome: v.result.outcome,
    steps: v.result.steps,
    ...(v.result.failingStep !== undefined ? { failingStep: v.result.failingStep } : {}),
    ...(v.result.failingStepName ? { failingStepName: v.result.failingStepName } : {}),
    ...(v.result.unavailableStep !== undefined ? { unavailableStep: v.result.unavailableStep } : {}),
    verifiedAt: v.result.verifiedAt,
    ansMode: v.result.ansMode,
    sessionId: v.sessionId,
    connectedAt: v.connectedAt,
    capabilities: v.capabilities,
    subscriptions: v.subscriptions,
    simulated: v.simulated,
  };
}

const toDisclosureDto = (d: DisclosureEntry): DisclosureDto => d;

/**
 * Everything the local SENSE server owns: the simulated world, the broker, the user's profile and
 * the audit trail. One instance per process.
 */
export class SenseContext {
  readonly actions: ActionDto[] = [];
  private readonly listeners = new Set<(e: ServerEvent) => void>();
  private actionCounter = 0;

  private constructor(
    readonly clock: Clock,
    readonly world: WorldSim,
    readonly broker: SenseBroker,
    readonly profiles: ProfileStore,
    readonly mode: ModeDto,
    /** Owner key for the signed portable profile. Stays on this device. */
    readonly ownerKeys: KeyPair,
  ) {
    broker.on('percept', (percept) => this.emit({ type: 'percept', percept }));
    broker.on('security', (event) => this.emit({ type: 'security', event }));
    broker.on('verification', (v) => this.emit({ type: 'verification', verification: toVerificationDto(v) }));
    broker.on('disclosure', (entry) => this.emit({ type: 'disclosure', entry: toDisclosureDto(entry) }));
    broker.on('ack', (a) => this.emit({ type: 'ack', perceptId: a.perceptId, via: a.via }));
    this.profiles.onChange((profile) => this.emit({ type: 'profile', profile }));
  }

  static async create(opts: ContextOptions = {}): Promise<SenseContext> {
    const env = opts.env ?? process.env;
    const clock = opts.clock ?? systemClock;
    const world = await WorldSim.create({ clock, seed: opts.seed ?? 1, ...(opts.keyDir ? { keyDir: opts.keyDir } : {}) });
    const ans = createAnsClient(env.ANS_MODE, world.registry, clock);
    const profiles = new ProfileStore();
    const broker = new SenseBroker({
      ans,
      transport: world.transport,
      clock,
      getProfile: () => profiles.current(),
      getPose: () => ({ position: world.user.position, headingDeg: world.user.headingDeg }),
    });
    const wantsLiveAi = env.SENSE_PROVIDER === 'anthropic' || (env.SENSE_PROVIDER !== 'mock' && Boolean(env.ANTHROPIC_API_KEY));
    const mode: ModeDto = {
      world: 'SIMULATED WORLD',
      ans: ans.mode === 'live' ? 'live ANS (not configured)' : 'ANS-modeled (simulated)',
      ai: wantsLiveAi ? 'ANTHROPIC' : 'MOCK AI',
      online: opts.online ?? false,
    };
    return new SenseContext(clock, world, broker, profiles, mode, await generateKeyPair());
  }

  // ── Events and audit trail ────────────────────────────────────────────────────────────────

  onEvent(fn: (e: ServerEvent) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  emit(e: ServerEvent): void {
    for (const fn of this.listeners) fn(e);
  }

  /** Record something the user did, and how (pointer, keyboard, or an InputDevice intent). */
  record(kind: string, via: string, detail: string): ActionDto {
    const action: ActionDto = { id: `a-${++this.actionCounter}`, timestamp: iso(this.clock), kind, via, detail };
    this.actions.push(action);
    this.emit({ type: 'action', action });
    return action;
  }

  snapshot(): StateSnapshot {
    const b = this.broker;
    return {
      mode: this.mode,
      profile: this.profiles.current(),
      percepts: b.allPercepts(),
      acknowledged: b
        .allPercepts()
        .filter((p) => b.isAcknowledged(p.id))
        .map((p) => p.id),
      security: b.securityEvents(),
      verifications: b.verifications().map(toVerificationDto),
      disclosure: b.disclosure.entries().map(toDisclosureDto),
      actions: [...this.actions],
      user: { place: this.world.user.place, position: this.world.user.position, headingDeg: this.world.user.headingDeg },
    };
  }

  /** Real-time upkeep: run the world's timeline and watch for silent safety feeds. */
  startBackground(): () => void {
    const tick = setInterval(() => void this.world.tick(), 1000);
    const watchdog = setInterval(() => this.broker.checkFreshness(), 5000);
    tick.unref();
    watchdog.unref();
    return () => {
      clearInterval(tick);
      clearInterval(watchdog);
    };
  }
}
