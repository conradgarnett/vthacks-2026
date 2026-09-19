import {
  AgentResponseSchema,
  HelloAckSchema,
  PAYLOAD_SCHEMAS,
  PushSchema,
  iso,
  signingPayload,
  type AgentRequest,
  type AgentTransport,
  type CapabilityId,
  type CapabilityResponse,
  type Clock,
  type Percept,
  type PushMessage,
  type SecurityEvent,
  type SenseCard,
  type SensoryProfile,
  type Tier,
} from '@sense/protocol';
import {
  certPublicKey,
  exportSpki,
  generateKeyPair,
  parseCertificate,
  randomB64,
  randomHex,
  verifyBytes,
  type AgentRecord,
  type AnsClient,
  type LiveChallenge,
  type VerificationResult,
} from '@sense/identity';
import { DisclosureLog, type DisclosureEntry } from './disclosure';
import { createPolicy, type Policy } from './policy';
import {
  accessibilityPercept,
  alarmPercepts,
  arrivalsPercept,
  fitShort,
  sourceSuffix,
  tierWord,
  type MappedPercept,
  type SourceInfo,
  type UserPose,
} from './percepts';
import { RateLimiter } from './ratelimit';
import { guardPercept } from './safety';
import { sanitizePayload, type PayloadSanitizeEvent } from './sanitize';
import { assessFreshness, reconcileClaims, tierFor, type Claim, type Freshness } from './trust';

export interface BrokerDeps {
  ans: AnsClient;
  transport: AgentTransport;
  clock: Clock;
  /** Local-only profile accessor. Its contents are never sent to a remote agent. */
  getProfile: () => SensoryProfile;
  /** Where the user is. In the simulation this is the sim's user pose. */
  getPose: () => UserPose;
  policy?: Policy;
}

export interface DataRecord {
  fqdn: string;
  label: string;
  capability: CapabilityId;
  /** Schema-validated and sanitized payload. */
  payload: unknown;
  tier: Tier;
  asOf: string;
  receivedAt: string;
  freshness: Freshness;
  simulated: boolean;
  source: SourceInfo;
}

export interface VerificationView {
  fqdn: string;
  displayName: string;
  result: VerificationResult;
  sessionId: string;
  connectedAt: string;
  capabilities: CapabilityId[];
  subscriptions: CapabilityId[];
  simulated: boolean;
}

export interface BrokerEvents {
  percept: Percept;
  security: SecurityEvent;
  verification: VerificationView;
  disclosure: DisclosureEntry;
  data: DataRecord;
  ack: { perceptId: string; via: string };
}

type Handlers = { [K in keyof BrokerEvents]?: Array<(v: BrokerEvents[K]) => void> };

interface Session {
  fqdn: string;
  record: AgentRecord;
  sessionId: string;
  verification: VerificationResult;
  identityKey: CryptoKey | undefined;
  card: SenseCard | undefined;
  connectedAt: number;
  subscriptions: Map<string, CapabilityId>;
  lastMessageAt: Map<CapabilityId, number>;
  silent: Set<CapabilityId>;
  stale: Set<CapabilityId>;
}

const SESSION_TTL_MS = 5 * 60_000;
const ARRIVAL_FETCH: CapabilityId[] = ['indoor-map', 'accessibility-features'];
const ARRIVAL_SUBSCRIBE: CapabilityId[] = ['alarm-feed', 'air-quality'];

/**
 * The SENSE broker agent: discovers publisher agents, verifies who they are, talks to them with
 * minimum-necessary ephemeral sessions, treats everything they send as untrusted data, and turns
 * it into percepts with visible provenance. It holds no global state about publishers; it works
 * with whichever verified agents are in range.
 */
export class SenseBroker {
  readonly policy: Policy;
  readonly disclosure: DisclosureLog;
  private readonly sessions = new Map<string, Session>();
  private readonly pushUnsub = new Map<string, () => void>();
  private readonly handlers: Handlers = {};
  private readonly limiter: RateLimiter;
  private readonly latestData = new Map<string, DataRecord>();
  private readonly percepts: Percept[] = [];
  private readonly securityLog: SecurityEvent[] = [];
  private readonly acked = new Set<string>();
  private readonly claims = new Map<string, Map<string, Claim>>();
  private readonly conflictShown = new Set<string>();
  private readonly lastRateEvent = new Map<string, number>();
  private readonly limitedCounts = new Map<string, number>();
  private counter = 0;

  constructor(private readonly deps: BrokerDeps) {
    this.policy = deps.policy ?? createPolicy();
    this.disclosure = new DisclosureLog(deps.clock, deps.getProfile);
    this.limiter = new RateLimiter(deps.clock);
    deps.transport.onUnsolicited(
      (fqdn, raw) =>
        void this.handleUnsolicited(fqdn, raw).catch((err) =>
          this.security({
            kind: 'CONTENT_REJECTED',
            source: fqdn,
            message: `An unsolicited message from ${fqdn} could not be processed (${errMsg(err)}) and was discarded.`,
          }),
        ),
    );
  }

  // ── Events ────────────────────────────────────────────────────────────────────────────────

  on<K extends keyof BrokerEvents>(event: K, fn: (v: BrokerEvents[K]) => void): () => void {
    const list = (this.handlers[event] ??= []) as Array<(v: BrokerEvents[K]) => void>;
    list.push(fn);
    return () => {
      const i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    };
  }

  private emit<K extends keyof BrokerEvents>(event: K, value: BrokerEvents[K]): void {
    for (const fn of (this.handlers[event] ?? []) as Array<(v: BrokerEvents[K]) => void>) fn(value);
  }

  private id(prefix: string): string {
    return `${prefix}-${++this.counter}`;
  }

  private nowIso(): string {
    return iso(this.deps.clock);
  }

  // ── Read models for the UI ────────────────────────────────────────────────────────────────

  allPercepts(): Percept[] {
    return [...this.percepts];
  }
  securityEvents(): SecurityEvent[] {
    return [...this.securityLog];
  }
  isAcknowledged(id: string): boolean {
    return this.acked.has(id);
  }

  verifications(): VerificationView[] {
    return [...this.sessions.values()].map((s) => this.view(s));
  }

  latest(fqdn: string, capability: CapabilityId): DataRecord | undefined {
    return this.latestData.get(`${fqdn}|${capability}`);
  }

  latestFor(capability: CapabilityId): DataRecord[] {
    return [...this.latestData.values()].filter((d) => d.capability === capability);
  }

  private view(s: Session): VerificationView {
    return {
      fqdn: s.fqdn,
      displayName: s.record.displayName,
      result: s.verification,
      sessionId: s.sessionId,
      connectedAt: new Date(s.connectedAt).toISOString(),
      capabilities: s.record.capabilities,
      subscriptions: [...new Set(s.subscriptions.values())],
      simulated: s.record.endpoints.some((e) => e.url.startsWith('sim://')),
    };
  }

  // ── Security events, percepts ─────────────────────────────────────────────────────────────

  private security(e: Omit<SecurityEvent, 'id' | 'timestamp'>): SecurityEvent {
    const event: SecurityEvent = { id: this.id('e'), timestamp: this.nowIso(), ...e };
    this.securityLog.push(event);
    if (this.securityLog.length > 500) this.securityLog.shift();
    this.emit('security', event);
    return event;
  }

  /** The only door out for percepts: guarded, logged and delivered to listeners. */
  emitPercept(p: Percept): Percept {
    const { percept, redacted } = guardPercept(p, { redact: true });
    if (redacted) {
      this.security({
        kind: 'INJECTION_NEUTRALIZED',
        source: percept.provenance.source,
        message: 'Reassuring wording inside publisher-supplied text was removed before display.',
      });
    }
    this.percepts.push(percept);
    if (this.percepts.length > 1000) this.percepts.shift();
    this.emit('percept', percept);
    return percept;
  }

  /** Percepts produced by SENSE's own senses (vision, hearing, taste, scent) enter here. */
  publishPercept(p: Percept): Percept {
    return this.emitPercept(p);
  }

  nextPerceptId(): string {
    return this.id('p');
  }

  acknowledge(perceptId: string, via: string): boolean {
    if (!this.percepts.some((p) => p.id === perceptId)) return false;
    this.acked.add(perceptId);
    this.emit('ack', { perceptId, via });
    return true;
  }

  // ── Discovery and connection ──────────────────────────────────────────────────────────────

  async discover(query: { area?: string; capability?: CapabilityId }): Promise<AgentRecord[]> {
    return this.deps.ans.search(query);
  }

  private sourceInfo(s: Session, tier: Tier, freshness?: Freshness): SourceInfo {
    void freshness;
    return {
      fqdn: s.fqdn,
      label: s.record.displayName.replace(/\s*\(simulated\)\s*$/i, ''),
      tier,
      simulated: s.record.endpoints.some((e) => e.url.startsWith('sim://')),
      agentVersion: s.record.version,
      verifiedAt: s.verification.verifiedAt,
      evidence: this.evidenceLines(s.verification),
    };
  }

  private evidenceLines(v: VerificationResult): string[] {
    const passed = v.steps.filter((x) => x.status === 'pass').length;
    const lines = [`${passed}/${v.steps.length} identity checks passed (${v.ansMode === 'simulated' ? 'ANS-modeled' : 'live ANS'})`];
    for (const step of v.steps) {
      if (step.status === 'fail' || step.status === 'unavailable') {
        lines.push(`step ${step.step} ${step.status}: ${step.evidence[0] ?? step.name}`);
      }
    }
    return lines;
  }

  /**
   * Resolve, say hello with a fresh ephemeral session identity, prove the agent controls its
   * keys, and run the ordered verification pipeline. Reuses a recent session unless `force`.
   */
  async connect(fqdn: string, opts: { force?: boolean } = {}): Promise<VerificationView> {
    const existing = this.sessions.get(fqdn);
    if (existing && !opts.force && this.deps.clock.now() - existing.connectedAt < SESSION_TTL_MS) {
      return this.view(existing);
    }
    let record: AgentRecord;
    try {
      record = await this.deps.ans.resolve(fqdn);
    } catch (err) {
      this.security({
        kind: 'SOURCE_OFFLINE',
        source: fqdn,
        message: `${fqdn} did not resolve: ${errMsg(err)}`,
      });
      throw err;
    }

    // Ephemeral per-session identity: random id and key, never reused, carrying nothing about the user.
    const sessionId = randomHex(16);
    const ephemeralPublicKey = await exportSpki((await generateKeyPair()).publicKey);
    const nonce = randomB64(24);
    const hello: AgentRequest = {
      v: 1,
      id: this.id('m'),
      sessionId,
      ts: this.nowIso(),
      type: 'hello',
      ephemeralPublicKey,
      nonce,
      protocols: ['sense/0.1'],
    };

    let raw: unknown;
    try {
      raw = await this.send(fqdn, hello);
    } catch (err) {
      this.security({
        kind: 'SOURCE_OFFLINE',
        source: fqdn,
        message: `${fqdn} is unreachable: ${errMsg(err)}`,
      });
      throw err;
    }

    const ack = HelloAckSchema.safeParse(raw);
    let verification: VerificationResult;
    let identityKey: CryptoKey | undefined;
    let card: SenseCard | undefined;
    if (!ack.success) {
      verification = this.synthRejection(record, 1, 'hello response was not a valid hello_ack');
    } else {
      const challenge: LiveChallenge = {
        fqdn: ack.data.fqdn,
        sessionId,
        ephemeralPublicKey,
        nonce,
        presentation: ack.data.presentation,
      };
      verification = await this.deps.ans.verify(record, challenge);
      if (verification.outcome !== 'REJECTED') {
        try {
          identityKey = await certPublicKey(parseCertificate(ack.data.presentation.identityCertPem));
          card = ack.data.presentation.senseCard.card;
        } catch {
          verification = this.synthRejection(record, 3, 'identity certificate could not be used');
        }
      }
    }

    const session: Session = {
      fqdn,
      record,
      sessionId,
      verification,
      identityKey,
      card,
      connectedAt: this.deps.clock.now(),
      subscriptions: new Map(),
      lastMessageAt: new Map(),
      silent: new Set(),
      stale: new Set(),
    };
    this.dropSession(fqdn);
    this.sessions.set(fqdn, session);
    const view = this.view(session);
    this.emit('verification', view);

    if (verification.outcome === 'REJECTED') {
      this.security({
        kind: 'IDENTITY_REJECTED',
        source: fqdn,
        ...(verification.failingStep ? { failingStep: verification.failingStep } : {}),
        ...(verification.failingStepName ? { failingStepName: verification.failingStepName } : {}),
        message: `Rejected ${fqdn} at step ${verification.failingStep}: ${verification.steps.find((s) => s.step === verification.failingStep)?.evidence[0] ?? 'failed'}. Nothing it sends will be used.`,
      });
    }
    return view;
  }

  private synthRejection(record: AgentRecord, step: number, why: string): VerificationResult {
    return {
      fqdn: record.fqdn,
      outcome: 'REJECTED',
      steps: [{ step, name: 'Handshake', status: 'fail', evidence: [why] }],
      failingStep: step,
      failingStepName: 'Handshake',
      verifiedAt: this.nowIso(),
      ansMode: this.deps.ans.mode,
    };
  }

  private dropSession(fqdn: string): void {
    this.pushUnsub.get(fqdn)?.();
    this.pushUnsub.delete(fqdn);
    this.sessions.delete(fqdn);
  }

  /** Every outbound request goes through the disclosure gate, then the transport. */
  private async send(fqdn: string, msg: AgentRequest): Promise<unknown> {
    const entry = this.disclosure.approve(fqdn, msg);
    this.emit('disclosure', entry);
    return this.deps.transport.request(fqdn, msg);
  }

  // ── Queries ───────────────────────────────────────────────────────────────────────────────

  /** Ask a connected agent for a capability. Returns null when nothing usable came back. */
  async query(fqdn: string, capability: CapabilityId, params?: Record<string, string | number | boolean>): Promise<DataRecord | null> {
    const session = this.usable(fqdn);
    if (!session) return null;
    const scope = [...(this.policy.scopes[capability] ?? [])];
    const req: AgentRequest = {
      v: 1,
      id: this.id('m'),
      sessionId: session.sessionId,
      ts: this.nowIso(),
      type: 'capability_query',
      capability,
      scope,
      ...(params ? { params } : {}),
    };
    let raw: unknown;
    try {
      raw = await this.send(fqdn, req);
    } catch (err) {
      this.security({
        kind: 'SOURCE_OFFLINE',
        source: fqdn,
        message: `${fqdn} did not answer: ${errMsg(err)}. Falling back to what SENSE can infer itself.`,
      });
      return null;
    }
    const parsed = AgentResponseSchema.safeParse(raw);
    if (!parsed.success) {
      this.security({
        kind: 'CONTENT_REJECTED',
        source: fqdn,
        message: 'Response failed schema validation and was discarded.',
      });
      return null;
    }
    const msg = parsed.data;
    if (msg.type === 'error') return null;
    if (msg.type !== 'capability_response' || msg.capability !== capability || msg.sessionId !== session.sessionId) {
      this.security({
        kind: 'CONTENT_REJECTED',
        source: fqdn,
        message: 'Unexpected response type or session; discarded.',
      });
      return null;
    }
    return this.ingest(session, msg, 'query');
  }

  private usable(fqdn: string): Session | undefined {
    const s = this.sessions.get(fqdn);
    return s && s.verification.outcome !== 'REJECTED' ? s : undefined;
  }

  // ── Subscriptions ─────────────────────────────────────────────────────────────────────────

  async subscribe(fqdn: string, capability: CapabilityId): Promise<string | null> {
    const session = this.usable(fqdn);
    if (!session) return null;
    if (!this.pushUnsub.has(fqdn)) {
      this.pushUnsub.set(
        fqdn,
        this.deps.transport.onPush(fqdn, (raw) => void this.handlePush(fqdn, raw)),
      );
    }
    const req: AgentRequest = {
      v: 1,
      id: this.id('m'),
      sessionId: session.sessionId,
      ts: this.nowIso(),
      type: 'subscribe',
      capability,
      scope: [...(this.policy.scopes[capability] ?? [])],
    };
    const raw = await this.send(fqdn, req).catch(() => undefined);
    const parsed = AgentResponseSchema.safeParse(raw);
    if (!parsed.success || parsed.data.type !== 'subscribe_ack') return null;
    session.subscriptions.set(parsed.data.subscriptionId, capability);
    session.lastMessageAt.set(capability, this.deps.clock.now());
    return parsed.data.subscriptionId;
  }

  async unsubscribe(fqdn: string, subscriptionId: string): Promise<boolean> {
    const session = this.usable(fqdn);
    if (!session?.subscriptions.has(subscriptionId)) return false;
    const req: AgentRequest = {
      v: 1,
      id: this.id('m'),
      sessionId: session.sessionId,
      ts: this.nowIso(),
      type: 'unsubscribe',
      subscriptionId,
    };
    const raw = await this.send(fqdn, req).catch(() => undefined);
    const parsed = AgentResponseSchema.safeParse(raw);
    if (parsed.success && parsed.data.type === 'unsubscribe_ack') {
      session.subscriptions.delete(subscriptionId);
      return true;
    }
    return false;
  }

  /** Push handlers run detached from any caller, so they must never throw: fail loud via the log. */
  private async handlePush(fqdn: string, raw: unknown): Promise<void> {
    try {
      await this.handlePushUnsafe(fqdn, raw);
    } catch (err) {
      this.security({
        kind: 'CONTENT_REJECTED',
        source: fqdn,
        message: `An update from ${fqdn} could not be processed (${errMsg(err)}) and was discarded.`,
      });
    }
  }

  private async handlePushUnsafe(fqdn: string, raw: unknown): Promise<void> {
    const parsed = PushSchema.safeParse(raw);
    const session = this.usable(fqdn);
    if (!parsed.success || !session) {
      this.security({
        kind: 'CONTENT_REJECTED',
        source: fqdn,
        message: 'Push failed validation or has no live session; discarded.',
      });
      return;
    }
    const msg = parsed.data;
    const cap = session.subscriptions.get(msg.subscriptionId);
    if (!cap || cap !== msg.capability || msg.sessionId !== session.sessionId) {
      this.security({
        kind: 'CONTENT_REJECTED',
        source: fqdn,
        message: 'Push does not match an active subscription of this session; discarded.',
      });
      return;
    }
    await this.ingest(session, msg, 'push');
  }

  /**
   * Pushes from agents we never subscribed to are unauthenticated noise. We verify the sender
   * only to report who it is, and NEVER route the content, so a spoofed "false alarm" cannot
   * suppress a real alarm.
   */
  private async handleUnsolicited(fqdn: string, raw: unknown): Promise<void> {
    const looksLikeClear = /cleared|false alarm|all clear|ignore/i.test(JSON.stringify(raw ?? ''));
    let view: VerificationView | undefined;
    try {
      view = await this.connect(fqdn, { force: true });
    } catch {
      /* unreachable sender: still report below */
    }
    if (view?.result.outcome !== 'REJECTED') {
      this.security({
        kind: 'CONTENT_REJECTED',
        source: fqdn,
        message: `Ignored an unsolicited push from ${fqdn}: SENSE only accepts updates from feeds it subscribed to.`,
      });
    }
    if (looksLikeClear) {
      this.security({
        kind: 'SPOOF_SUPPRESSION_BLOCKED',
        source: fqdn,
        message: `An unsolicited message from ${fqdn} tried to cancel or dismiss an alarm. It was not used. Existing alarms from verified sources stay active.`,
      });
    }
  }

  // ── Ingest: signature, schema, sanitize, freshness, tier, route ──────────────────────────

  private async ingest(session: Session, msg: CapabilityResponse | PushMessage, via: 'query' | 'push'): Promise<DataRecord | null> {
    const fqdn = session.fqdn;
    const capability = msg.capability;

    if (!session.identityKey || !(await verifyBytes(session.identityKey, signingPayload(msg), msg.signature))) {
      this.security({
        kind: 'SIGNATURE_INVALID',
        source: fqdn,
        message: `A ${via} from ${fqdn} was not signed by its verified identity key and was discarded.`,
      });
      return null;
    }

    const schema = PAYLOAD_SCHEMAS[capability];
    const validated = schema.safeParse(msg.data);
    if (!validated.success) {
      const issue = validated.error.issues[0];
      this.security({
        kind: 'CONTENT_REJECTED',
        source: fqdn,
        message: `${capability} payload from ${fqdn} failed schema validation (${issue?.path.join('.') || 'root'}: ${issue?.message ?? 'invalid'}) and was discarded.`,
      });
      return null;
    }

    const { value, events } = sanitizePayload(validated.data, {
      maxLen: this.policy.maxFieldLength,
    });
    this.reportSanitizer(fqdn, capability, events);

    const now = this.deps.clock.now();
    const timestamps = payloadTimestamps(capability, value, msg.asOf);
    const cardCap = session.card?.capabilities.find((c) => c.id === capability);
    const freshness = assessFreshness(cardCap, timestamps, now);
    const tier = tierFor(session.verification.outcome, freshness);
    session.lastMessageAt.set(capability, now);

    if (session.silent.delete(capability)) {
      this.emitPercept({
        id: this.id('p'),
        timestamp: this.nowIso(),
        sense: capability === 'air-quality' ? 'smell' : 'hearing',
        kind: 'status',
        urgency: 1,
        short: fitShort(`${humanCap(capability)} restored.`, sourceSuffix(this.sourceInfo(session, tier))),
        provenance: this.provenance(session, tier, freshness),
        safety: true,
        simulated: session.record.endpoints.some((e) => e.url.startsWith('sim://')),
      });
    }

    const source = this.sourceInfo(session, tier);
    if (freshness.stale && !session.stale.has(capability)) {
      session.stale.add(capability);
      this.security({
        kind: 'STALE_DOWNGRADED',
        source: fqdn,
        message: `${source.label} ${humanCap(capability).toLowerCase()} is ${freshness.ageSeconds}s old (limit ${freshness.maxAgeSeconds}s). Downgraded from VERIFIED to UNVERIFIED.`,
      });
    } else if (!freshness.stale) {
      session.stale.delete(capability);
    }

    const record: DataRecord = {
      fqdn,
      label: source.label,
      capability,
      payload: value,
      tier,
      asOf: msg.asOf,
      receivedAt: this.nowIso(),
      freshness,
      simulated: source.simulated,
      source,
    };
    this.latestData.set(`${fqdn}|${capability}`, record);
    this.emit('data', record);
    this.route(record);
    return record;
  }

  private provenance(session: Session, tier: Tier, freshness?: Freshness) {
    const s = this.sourceInfo(session, tier);
    return {
      tier,
      source: s.fqdn,
      sourceLabel: s.label,
      agentVersion: s.agentVersion,
      verifiedAt: s.verifiedAt,
      evidence: [...s.evidence, ...(freshness ? [`data age ${freshness.ageSeconds}s`] : [])],
    };
  }

  private reportSanitizer(fqdn: string, capability: CapabilityId, events: PayloadSanitizeEvent[]): void {
    for (const e of events) {
      if (e.neutralized) {
        this.security({
          kind: 'INJECTION_NEUTRALIZED',
          source: fqdn,
          message: `Removed instruction-like or reassuring text from ${capability} field "${e.path}" (${e.matched.join(', ')}). It was treated as data and never obeyed.`,
        });
      }
    }
  }

  // ── Routing of payloads into percepts ─────────────────────────────────────────────────────

  private route(rec: DataRecord): void {
    const now = this.nowIso();
    const nextId = () => this.id('p');
    const freshness = rec.freshness;
    switch (rec.capability) {
      case 'alarm-feed':
        for (const m of alarmPercepts(rec.payload as never, {
          source: rec.source,
          pose: this.deps.getPose(),
          freshness,
          nextId,
          now,
        })) {
          this.admit(m, rec);
        }
        break;
      case 'arrivals':
        this.emitPercept(arrivalsPercept(rec.payload as never, { source: rec.source, freshness, nextId, now }));
        break;
      case 'accessibility-features':
        this.emitPercept(
          accessibilityPercept(rec.payload as never, {
            source: rec.source,
            freshness,
            nextId,
            now,
          }),
        );
        break;
      default:
        // indoor-map, air-quality, menu-allergens and device-control are consumed by sense modules
        // through the 'data' event; they do not become percepts on their own.
        break;
    }
  }

  private admit(m: MappedPercept, rec: DataRecord): void {
    const p = m.percept;
    const decision = this.limiter.admit({
      source: rec.fqdn,
      key: m.key,
      urgency: p.urgency,
      tier: p.provenance.tier,
    });
    if (!decision.admit) {
      if (decision.reason === 'rate-limited') this.reportRateLimit(rec);
      return;
    }
    this.emitPercept(p);
    this.recordClaim(m, rec);
  }

  private reportRateLimit(rec: DataRecord): void {
    const now = this.deps.clock.now();
    const n = (this.limitedCounts.get(rec.fqdn) ?? 0) + 1;
    this.limitedCounts.set(rec.fqdn, n);
    if (now - (this.lastRateEvent.get(rec.fqdn) ?? -Infinity) < 5000) return;
    this.lastRateEvent.set(rec.fqdn, now);
    this.security({
      kind: 'RATE_LIMITED',
      source: rec.fqdn,
      message: `${rec.label} is sending alerts faster than they can be read. Duplicates are collapsed and routine ones held back (${n} so far). Urgent alerts from verified sources are never held back.`,
    });
  }

  /** Track per-topic claims; when sources disagree, say so and keep the higher-trust one first. */
  private recordClaim(m: MappedPercept, rec: DataRecord): void {
    const byTopic = this.claims.get(m.topic) ?? new Map<string, Claim>();
    byTopic.set(rec.fqdn, { topic: m.topic, value: m.value, percept: m.percept });
    this.claims.set(m.topic, byTopic);
    const { conflicts } = reconcileClaims([...byTopic.values()]);
    for (const c of conflicts) {
      const key = `${c.topic}|${c.leader.value}|${c.others.map((o) => o.value).join(',')}`;
      if (this.conflictShown.has(key)) continue;
      this.conflictShown.add(key);
      this.security({
        kind: 'SOURCE_CONFLICT',
        source: c.leader.percept.provenance.source,
        message: c.message,
      });
      this.emitPercept({
        id: this.id('p'),
        timestamp: this.nowIso(),
        sense: 'hearing',
        kind: 'status',
        urgency: Math.max(c.leader.percept.urgency, 3),
        short: fitShort(
          'Sources disagree.',
          `${tierWord(c.leader.percept.provenance.tier)}, ${c.leader.percept.provenance.sourceLabel ?? c.leader.percept.provenance.source} leads.`,
        ),
        long: c.message,
        provenance: {
          ...c.leader.percept.provenance,
          evidence: [...c.leader.percept.provenance.evidence, 'conflict policy: higher tier leads, nothing discarded'],
        },
        safety: true,
        simulated: c.leader.percept.simulated ?? false,
      });
    }
  }

  // ── Arrival flow ──────────────────────────────────────────────────────────────────────────

  /**
   * Arrive somewhere: discover agents by area, verify each, fetch static data, subscribe to
   * safety feeds, and announce what is connected and how far each source is trusted.
   */
  async arrive(area: string): Promise<VerificationView[]> {
    const records = await this.discover({ area });
    const views: VerificationView[] = [];
    for (const r of records) {
      const view = await this.connect(r.fqdn, { force: true }).catch(() => undefined);
      if (!view) continue;
      views.push(view);
      const s = this.sessions.get(r.fqdn);
      if (!s || view.result.outcome === 'REJECTED') continue;
      for (const cap of ARRIVAL_FETCH) if (r.capabilities.includes(cap)) await this.query(r.fqdn, cap);
      for (const cap of ARRIVAL_SUBSCRIBE) if (r.capabilities.includes(cap)) await this.subscribe(r.fqdn, cap);
      this.announceConnection(s);
    }
    return views;
  }

  private announceConnection(s: Session): void {
    const v = s.verification;
    const passed = v.steps.filter((x) => x.status === 'pass').length;
    const tier: Tier = v.outcome === 'VERIFIED' ? 'VERIFIED' : 'UNVERIFIED';
    const source = this.sourceInfo(s, tier);
    const problem = v.steps.find((x) => x.status === 'unavailable');
    this.emitPercept({
      id: this.id('p'),
      timestamp: this.nowIso(),
      sense: 'vision',
      kind: 'status',
      urgency: tier === 'VERIFIED' ? 1 : 2,
      short: fitShort(`${source.label}: ${tierWord(tier)}, ${passed} of ${v.steps.length} checks.`),
      long: `${source.label} (${s.fqdn}) connected. Identity ${tier === 'VERIFIED' ? 'verified' : 'only partly verified'} with ${passed} of ${v.steps.length} checks (${v.ansMode === 'simulated' ? 'ANS-modeled, simulated registry' : 'live ANS'}).${problem ? ` Step ${problem.step} could not run: ${problem.evidence[0]}. SENSE treats this source as UNVERIFIED.` : ''} Offers: ${s.record.capabilities.join(', ')}.`,
      provenance: {
        tier,
        source: s.fqdn,
        sourceLabel: source.label,
        agentVersion: source.agentVersion,
        verifiedAt: source.verifiedAt,
        evidence: source.evidence,
      },
      simulated: source.simulated,
    });
  }

  // ── Watchdog ──────────────────────────────────────────────────────────────────────────────

  /**
   * Fail loud, not silent: a subscribed safety feed that goes quiet past its declared freshness
   * is reported as unknown, never assumed unchanged. Call periodically.
   */
  checkFreshness(): void {
    const now = this.deps.clock.now();
    for (const s of this.sessions.values()) {
      if (s.verification.outcome === 'REJECTED') continue;
      for (const cap of new Set(s.subscriptions.values())) {
        const limit = s.card?.capabilities.find((c) => c.id === cap)?.freshness.maxAgeSeconds;
        if (!limit || s.silent.has(cap)) continue;
        const quiet = (now - (s.lastMessageAt.get(cap) ?? s.connectedAt)) / 1000;
        if (quiet <= limit) continue;
        s.silent.add(cap);
        const source = this.sourceInfo(s, 'UNVERIFIED');
        this.security({
          kind: 'SOURCE_OFFLINE',
          source: s.fqdn,
          message: `${source.label} ${humanCap(cap).toLowerCase()} has been silent for ${Math.round(quiet)}s (limit ${limit}s). Its current state is unknown.`,
        });
        this.emitPercept({
          id: this.id('p'),
          timestamp: this.nowIso(),
          sense: cap === 'air-quality' ? 'smell' : 'hearing',
          kind: 'status',
          urgency: 3,
          short: fitShort(`${humanCap(cap)} silent.`, sourceSuffix(source)),
          long: `${source.label} has not sent ${humanCap(cap).toLowerCase()} updates for ${Math.round(quiet)} seconds (its declared limit is ${limit}). SENSE does not know the current state and is not assuming nothing has changed. Treat as UNVERIFIED and check another way.`,
          provenance: this.provenance(s, 'UNVERIFIED'),
          safety: true,
          simulated: source.simulated,
        });
      }
    }
  }
}

function humanCap(c: CapabilityId): string {
  return { 'alarm-feed': 'Alarm feed', 'air-quality': 'Air quality feed' }[c as 'alarm-feed'] ?? c.replace(/-/g, ' ');
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : 'unknown error';
}

/** The timestamps that decide freshness: measurements carry their own; otherwise the message's asOf. */
function payloadTimestamps(capability: CapabilityId, payload: unknown, asOf: string): string[] {
  if (capability === 'air-quality') {
    const r = (payload as { readings: { measuredAt: string }[] }).readings;
    return r.length > 0 ? r.map((x) => x.measuredAt) : [asOf];
  }
  return [asOf];
}
