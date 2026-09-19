/**
 * WebSocket client with reconnect.
 *
 * The user can't see a disconnect indicator, so connection state changes are
 * surfaced to the caller to be spoken rather than shown.
 */

export type ServerEvent =
  | { type: "ready"; provider: string; provider_active: string; demo_mode: boolean }
  | { type: "speech"; text: string }
  | { type: "trace"; trace_id: number; label: string; stages: Record<string, number>; total_ms: number }
  | { type: "hazard"; text: string; severity: number; azimuth_deg: number; distance_m: number }
  | { type: "beacon"; label: string; azimuth_deg: number; distance_m: number; visible: boolean }
  | { type: "beacon_stop" };

type Handlers = {
  onEvent: (event: ServerEvent) => void;
  onConnectionChange: (connected: boolean) => void;
};

const MAX_BACKOFF_MS = 8000;

export class Connection {
  private socket: WebSocket | null = null;
  private backoff = 500;
  private closedByUs = false;

  constructor(private url: string, private handlers: Handlers) {}

  connect(): void {
    this.closedByUs = false;
    this.socket = new WebSocket(this.url);
    this.socket.binaryType = "arraybuffer";

    this.socket.onopen = () => {
      this.backoff = 500;
      this.handlers.onConnectionChange(true);
    };

    this.socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        this.handlers.onEvent(JSON.parse(event.data) as ServerEvent);
      } catch {
        // A malformed frame is not worth tearing down the session for.
      }
    };

    this.socket.onclose = () => {
      this.handlers.onConnectionChange(false);
      if (!this.closedByUs) this.scheduleReconnect();
    };

    // onerror always precedes onclose; reconnect is handled there.
    this.socket.onerror = () => this.socket?.close();
  }

  private scheduleReconnect(): void {
    setTimeout(() => this.connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS);
  }

  get isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  sendFrame(blob: Blob): void {
    if (!this.isOpen) return;
    blob.arrayBuffer().then((buf) => {
      if (this.isOpen) this.socket!.send(buf);
    });
  }

  sendIntent(type: string, text?: string): void {
    if (!this.isOpen) return;
    this.socket!.send(JSON.stringify(text ? { type, text } : { type }));
  }

  close(): void {
    this.closedByUs = true;
    this.socket?.close();
  }
}
