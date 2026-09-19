import type { ServerEvent, StateSnapshot } from '@sense/protocol';

export type ConnectionStatus = 'connecting' | 'open' | 'closed';

/** How the web app talks to the local SENSE server. Tests supply a fake. */
export interface SenseApi {
  getState(): Promise<StateSnapshot>;
  /** POST JSON. `via` says how the user did it ("pointer", "keyboard", "intent:<driver>"). */
  post(path: string, body?: unknown, via?: string): Promise<unknown>;
  get(path: string): Promise<unknown>;
  connect(onEvent: (e: ServerEvent) => void, onStatus: (s: ConnectionStatus) => void): () => void;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function createHttpApi(base = ''): SenseApi {
  async function request(path: string, init?: RequestInit): Promise<unknown> {
    const res = await fetch(`${base}${path}`, init);
    const text = await res.text();
    const json: unknown = text ? JSON.parse(text) : null;
    if (!res.ok) throw new ApiError((json as { error?: string } | null)?.error ?? res.statusText, res.status);
    return json;
  }
  return {
    getState: () => request('/api/state') as Promise<StateSnapshot>,
    get: (path) => request(path),
    post: (path, body, via = 'pointer') =>
      request(path, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-sense-via': via },
        body: JSON.stringify(body ?? {}),
      }),
    connect(onEvent, onStatus) {
      let socket: WebSocket | undefined;
      let closed = false;
      let retry = 500;
      const open = () => {
        onStatus('connecting');
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        socket = new WebSocket(`${proto}://${location.host}/ws`);
        socket.onopen = () => {
          retry = 500;
          onStatus('open');
        };
        socket.onmessage = (m) => {
          try {
            onEvent(JSON.parse(String(m.data)) as ServerEvent);
          } catch {
            /* ignore malformed frames */
          }
        };
        socket.onclose = () => {
          onStatus('closed');
          if (!closed) setTimeout(open, (retry = Math.min(retry * 2, 8000)));
        };
      };
      open();
      return () => {
        closed = true;
        socket?.close();
      };
    },
  };
}
