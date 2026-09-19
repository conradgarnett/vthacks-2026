import type { AgentRequest } from './messages';

/**
 * How the SENSE broker reaches publisher agents. Everything that comes back is UNTRUSTED `unknown`
 * and must be parsed with the schemas in messages.ts / payloads.ts before use.
 *
 * The simulation implements this in-process with a hostname map (fqdn -> agent). An HTTP wrapper
 * exposes the same agents for inspection; see docs/ARCHITECTURE.md.
 */
export interface AgentTransport {
  /** Send a request to the agent at `fqdn` and return its raw response. Rejects if unreachable. */
  request(fqdn: string, msg: AgentRequest): Promise<unknown>;
  /** Receive raw push messages from an agent the broker subscribed to. Returns an unsubscribe fn. */
  onPush(fqdn: string, handler: (raw: unknown) => void): () => void;
  /**
   * Receive pushes from agents the broker never subscribed to. Anything arriving here is
   * unauthenticated until the sender passes verification. Returns an unsubscribe fn.
   */
  onUnsolicited(handler: (fqdn: string, raw: unknown) => void): () => void;
}
