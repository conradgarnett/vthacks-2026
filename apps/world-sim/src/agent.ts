import {
  AgentRequestSchema,
  iso,
  systemClock,
  type CapabilityId,
  type CapabilityResponse,
  type Clock,
  type ErrorMessage,
  type PushMessage,
} from '@sense/protocol';
import type { PublisherIdentity } from '@sense/identity';

export type Params = Record<string, string | number | boolean>;
export type Provider = (params: Params) => unknown | Promise<unknown>;

interface Subscription {
  id: string;
  sessionId: string;
  capability: CapabilityId;
}

/**
 * A simulated publisher agent: answers hello, capability queries, subscribe and unsubscribe, and
 * pushes signed updates to subscribers. Requests are validated and limited to declared scopes.
 */
export class SimAgent {
  private readonly subs = new Map<string, Subscription>();
  private readonly sinks = new Set<(raw: unknown) => void>();
  private seq = 0;
  private counter = 0;
  /** When true the agent is unreachable (simulates an outage). */
  offline = false;
  /** When true heartbeats stop, so a subscriber's feed goes silent. */
  muted = false;

  constructor(
    readonly identity: PublisherIdentity,
    readonly providers: Partial<Record<CapabilityId, Provider>>,
    private readonly clock: Clock = systemClock,
  ) {}

  get fqdn(): string {
    return this.identity.fqdn;
  }

  addSink(fn: (raw: unknown) => void): () => void {
    this.sinks.add(fn);
    return () => this.sinks.delete(fn);
  }

  subscribedCapabilities(): CapabilityId[] {
    return [...new Set([...this.subs.values()].map((s) => s.capability))];
  }

  private error(sessionId: string, code: ErrorMessage['code'], message: string): ErrorMessage {
    return {
      v: 1,
      id: `err-${++this.counter}`,
      sessionId,
      ts: iso(this.clock),
      type: 'error',
      code,
      message,
    };
  }

  async handle(raw: unknown): Promise<unknown> {
    if (this.offline) throw new Error(`${this.fqdn} is unreachable`);
    const parsed = AgentRequestSchema.safeParse(raw);
    if (!parsed.success) return this.error('unknown-session', 'bad_request', 'request failed validation');
    const req = parsed.data;
    switch (req.type) {
      case 'hello':
        return this.identity.hello(req);
      case 'capability_query': {
        const denied = this.checkScope(req.capability, req.scope);
        if (denied) return this.error(req.sessionId, 'scope_denied', denied);
        const provider = this.providers[req.capability];
        if (!provider) return this.error(req.sessionId, 'unsupported', `no ${req.capability} capability`);
        const data = await provider(req.params ?? {});
        return this.identity.sign<CapabilityResponse>({
          v: 1,
          id: `res-${++this.counter}`,
          sessionId: req.sessionId,
          ts: iso(this.clock),
          type: 'capability_response',
          capability: req.capability,
          asOf: iso(this.clock),
          data,
        });
      }
      case 'subscribe': {
        const denied = this.checkScope(req.capability, req.scope);
        if (denied) return this.error(req.sessionId, 'scope_denied', denied);
        const cap = this.identity.card().capabilities.find((c) => c.id === req.capability);
        if (!cap?.subscribable || !this.providers[req.capability]) {
          return this.error(req.sessionId, 'unsupported', `${req.capability} is not subscribable`);
        }
        const id = `sub-${++this.counter}`;
        this.subs.set(id, { id, sessionId: req.sessionId, capability: req.capability });
        // Send the current state right after the ack so a subscriber never starts blind.
        queueMicrotask(() => void this.pushTo(id));
        return {
          v: 1,
          id: `ack-${++this.counter}`,
          sessionId: req.sessionId,
          ts: iso(this.clock),
          type: 'subscribe_ack',
          subscriptionId: id,
          capability: req.capability,
        };
      }
      case 'unsubscribe': {
        const existed = this.subs.get(req.subscriptionId);
        if (!existed || existed.sessionId !== req.sessionId) {
          return this.error(req.sessionId, 'not_found', 'no such subscription for this session');
        }
        this.subs.delete(req.subscriptionId);
        return {
          v: 1,
          id: `ack-${++this.counter}`,
          sessionId: req.sessionId,
          ts: iso(this.clock),
          type: 'unsubscribe_ack',
          subscriptionId: req.subscriptionId,
        };
      }
    }
  }

  private checkScope(capability: CapabilityId, scope: string[]): string | undefined {
    const cap = this.identity.card().capabilities.find((c) => c.id === capability);
    if (!cap) return `${capability} is not offered by this agent`;
    const extra = scope.filter((s) => !cap.scopes.includes(s));
    return extra.length > 0 ? `scope not offered for ${capability}: ${extra.join(', ')}` : undefined;
  }

  private async pushTo(subscriptionId: string, data?: unknown): Promise<void> {
    const sub = this.subs.get(subscriptionId);
    if (!sub || this.offline) return;
    const provider = this.providers[sub.capability];
    const payload = data ?? (provider ? await provider({}) : undefined);
    const msg = await this.identity.sign<PushMessage>({
      v: 1,
      id: `push-${++this.counter}`,
      sessionId: sub.sessionId,
      ts: iso(this.clock),
      type: 'push',
      subscriptionId,
      capability: sub.capability,
      seq: this.seq++,
      asOf: iso(this.clock),
      data: payload,
    });
    for (const sink of this.sinks) sink(msg);
  }

  /** Push the current (or given) data for `capability` to every subscriber. */
  async push(capability: CapabilityId, data?: unknown): Promise<void> {
    if (this.muted && data === undefined) return;
    for (const sub of [...this.subs.values()]) {
      if (sub.capability === capability) await this.pushTo(sub.id, data);
    }
  }

  /** Publish a fully formed signed push to the unsolicited channel (attacker behaviour). */
  async pushUnsolicited(capability: CapabilityId, data: unknown, sessionId = 'unsolicited-session'): Promise<PushMessage> {
    const msg = await this.identity.sign<PushMessage>({
      v: 1,
      id: `push-${++this.counter}`,
      sessionId,
      ts: iso(this.clock),
      type: 'push',
      subscriptionId: 'sub-unsolicited',
      capability,
      seq: this.seq++,
      asOf: iso(this.clock),
      data,
    });
    return msg;
  }
}
