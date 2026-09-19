import type { ActionDto, Clock, DisclosureDto, ModeDto, ServerEvent, StateSnapshot, VerificationDto } from '@sense/protocol';
import { systemClock, iso } from '@sense/protocol';
import { generateKeyPair, createAnsClient, type KeyPair } from '@sense/identity';
import { SenseBroker, type DisclosureEntry, type VerificationView } from '@sense/core';
import { WorldSim } from '@sense/world-sim';
import { createProviders, type Providers } from '@sense/providers';
import { VisionSense, type MapSource } from '@sense/vision';
import { EchoSense } from '@sense/hearing';
import { ScentGuard, type ScentAlarm, type ScentSource } from '@sense/scent';
import { TasteLens } from '@sense/taste';
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

/** The current indoor map for a place, with the tier and evidence the broker assigned to it. */
export function mapSourceFor(broker: SenseBroker, fqdn: string): MapSource | undefined {
  const rec = broker.latest(fqdn, 'indoor-map');
  if (!rec || rec.tier === 'REJECTED') return undefined;
  return {
    fqdn: rec.fqdn,
    label: rec.label,
    tier: rec.tier,
    simulated: rec.simulated,
    agentVersion: rec.source.agentVersion,
    verifiedAt: rec.source.verifiedAt,
    evidence: rec.source.evidence,
    map: rec.payload as MapSource['map'],
  };
}

/** An ACTIVE alarm reported by a VERIFIED source right now, if any. Inferred sounds defer to it. */
export function activeVerifiedAlarm(broker: SenseBroker): { label: string; fqdn: string } | undefined {
  for (const d of broker.latestFor('alarm-feed')) {
    const alarms = (d.payload as { alarms: { state: string }[] }).alarms;
    if (d.tier === 'VERIFIED' && alarms.some((a) => a.state === 'active')) return { label: d.label, fqdn: d.fqdn };
  }
  return undefined;
}

/**
 * Turn the broker's latest verified feeds into ScentGuard inputs. A source's tier here is its
 * IDENTITY outcome; ScentGuard decides staleness itself from each reading's own timestamp.
 */
export function scentInputs(broker: SenseBroker): { sources: ScentSource[]; alarms: ScentAlarm[] } {
  const outcome = new Map(broker.verifications().map((v) => [v.fqdn, v.result.outcome]));
  const idTier = (fqdn: string): 'VERIFIED' | 'UNVERIFIED' => (outcome.get(fqdn) === 'VERIFIED' ? 'VERIFIED' : 'UNVERIFIED');
  const sources: ScentSource[] = broker.latestFor('air-quality').map((d) => ({
    fqdn: d.fqdn,
    label: d.label,
    tier: idTier(d.fqdn),
    simulated: d.simulated,
    agentVersion: d.source.agentVersion,
    verifiedAt: d.source.verifiedAt,
    evidence: d.source.evidence,
    maxAgeSeconds: d.freshness.maxAgeSeconds,
    readings: (d.payload as { readings: ScentSource['readings'] }).readings,
  }));
  const alarms: ScentAlarm[] = broker.latestFor('alarm-feed').map((d) => ({
    fqdn: d.fqdn,
    label: d.label,
    tier: idTier(d.fqdn),
    active: (d.payload as { alarms: { state: string }[] }).alarms.some((a) => a.state === 'active'),
  }));
  return { sources, alarms };
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
    readonly providers: Providers,
    readonly vision: VisionSense,
    readonly echo: EchoSense,
    readonly scent: ScentGuard,
    readonly taste: TasteLens,
    /** Owner key for the signed portable profile. Stays on this device. */
    readonly ownerKeys: KeyPair,
  ) {
    broker.on('percept', (percept) => this.emit({ type: 'percept', percept }));
    broker.on('security', (event) => this.emit({ type: 'security', event }));
    broker.on('verification', (v) => this.emit({ type: 'verification', verification: toVerificationDto(v) }));
    broker.on('disclosure', (entry) => this.emit({ type: 'disclosure', entry: toDisclosureDto(entry) }));
    broker.on('ack', (a) => this.emit({ type: 'ack', perceptId: a.perceptId, via: a.via }));
    this.profiles.onChange((profile) => this.emit({ type: 'profile', profile }));
    broker.on('data', (d) => {
      if (d.capability === 'air-quality' || d.capability === 'alarm-feed') this.updateScent();
    });
  }

  /** Re-evaluate smoke risk from the latest verified feeds and publish a percept if it changed. */
  updateScent(): void {
    const { sources, alarms } = scentInputs(this.broker);
    for (const p of this.scent.update({ sources, alarms })) this.broker.publishPercept(p);
    const status = this.scent.status();
    if (status) this.emit({ type: 'scent', scent: status });
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
    const providers = createProviders(env);
    const mode: ModeDto = {
      world: 'SIMULATED WORLD',
      ans: ans.mode === 'live' ? 'live ANS (not configured)' : 'ANS-modeled (simulated)',
      ai: providers.label,
      online: opts.online ?? false,
    };
    const vision = new VisionSense({
      provider: providers.vision,
      clock,
      nextId: () => broker.nextPerceptId(),
      getPose: () => ({ position: world.user.position, headingDeg: world.user.headingDeg }),
      getMap: () => mapSourceFor(broker, `${world.user.place}.sim`),
    });
    const echo = new EchoSense({
      clock,
      nextId: () => broker.nextPerceptId(),
      getProfile: () => profiles.current(),
      verifiedAlarm: () => activeVerifiedAlarm(broker),
    });
    const scent = new ScentGuard({
      clock,
      nextId: () => broker.nextPerceptId(),
      getPose: () => ({ position: world.user.position, headingDeg: world.user.headingDeg }),
    });
    const taste = new TasteLens({
      provider: providers.taste,
      clock,
      nextId: () => broker.nextPerceptId(),
      getProfile: () => profiles.current(),
    });
    return new SenseContext(clock, world, broker, profiles, mode, providers, vision, echo, scent, taste, await generateKeyPair());
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
      scent: this.scent.status(),
      user: { place: this.world.user.place, position: this.world.user.position, headingDeg: this.world.user.headingDeg },
    };
  }

  /** Real-time upkeep: run the world's timeline and watch for silent safety feeds. */
  startBackground(): () => void {
    const tick = setInterval(() => void this.world.tick(), 1000);
    const watchdog = setInterval(() => {
      this.broker.checkFreshness();
      this.updateScent();
    }, 5000);
    const poll = setInterval(() => void this.broker.pollUnsubscribed(), 60_000);
    poll.unref();
    tick.unref();
    watchdog.unref();
    return () => {
      clearInterval(tick);
      clearInterval(watchdog);
      clearInterval(poll);
    };
  }
}
