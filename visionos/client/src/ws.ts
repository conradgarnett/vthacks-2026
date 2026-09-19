/**
 * WebSocket client with reconnect.
 *
 * The user can't see a disconnect indicator, so connection state changes are
 * surfaced to the caller to be spoken rather than shown.
 */

export type ServerEvent =
  | {
      type: "ready";
      provider: string;
      provider_active: string;
      ocr?: string;
      device?: string;
      demo_mode: boolean;
    }
  | { type: "speech"; text: string }
  | {
      type: "trace";
      trace_id: number;
      label: string;
      stages: Record<string, number>;
      total_ms: number;
    }
  | { type: "hazard"; text: string; severity: number; azimuth_deg: number; distance_m: number }
  | { type: "beacon"; label: string; azimuth_deg: number; distance_m: number; visible: boolean }
  | { type: "beacon_stop" };

type Handlers = {
  onEvent: (event: ServerEvent) => void;
  onConnectionChange: (connected: boolean) => void;
};

const MAX_BACKOFF_MS = 8000;

// A read burst is one tagged message so the server reads it directly instead
// of feeding it to perception. Live frames are bare JPEGs, which start with
// 0xFFD8, so the tag can never be mistaken for one. Mirrors
// pack_read_frames() in backend/main.py.
const READ_TAG = new TextEncoder().encode("READ");

export class Connection {
  private socket: WebSocket | null = null;
  private backoff = 500;
  private closedByUs = false;
  // True once the server has actually spoken. The dev proxy accepts the
  // socket before the backend does, so onopen alone proves nothing: with the
  // backend down, every attempt opened and closed, and each one re-announced
  // "I lost connection" and reset the backoff to half a second.
  private established = false;

  constructor(private url: string, private handlers: Handlers) {}

  connect(): void {
    this.closedByUs = false;
    this.socket = new WebSocket(this.url);
    this.socket.binaryType = "arraybuffer";

    this.socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      let parsed: ServerEvent;
      try {
        parsed = JSON.parse(event.data) as ServerEvent;
      } catch {
        return; // A malformed frame is not worth tearing down the session for.
      }
      if (!this.established) {
        this.established = true;
        this.backoff = 500;
        this.handlers.onConnectionChange(true);
      }
      this.handlers.onEvent(parsed);
    };

    this.socket.onclose = () => {
      // Only a connection we had is a connection we lost. A failed attempt
      // is retried quietly, with growing backoff.
      if (this.established) {
        this.established = false;
        this.handlers.onConnectionChange(false);
      }
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
    return this.established && this.socket?.readyState === WebSocket.OPEN;
  }

  /** Live frame for perception. */
  sendFrame(blob: Blob): void {
    if (!this.isOpen) return;
    blob.arrayBuffer().then((buf) => {
      if (this.isOpen) this.socket!.send(buf);
    });
  }

  /**
   * A burst of detailed frames to read text from, as one message:
   * READ, then each frame as a big-endian u32 length plus its JPEG bytes.
   * Answered with speech.
   */
  sendReadFrames(blobs: Blob[]): void {
    if (!this.isOpen || blobs.length === 0) return;
    Promise.all(blobs.map((blob) => blob.arrayBuffer())).then((buffers) => {
      if (!this.isOpen) return;
      const total =
        READ_TAG.length + buffers.reduce((sum, buf) => sum + 4 + buf.byteLength, 0);
      const message = new Uint8Array(total);
      const view = new DataView(message.buffer);

      message.set(READ_TAG, 0);
      let offset = READ_TAG.length;
      for (const buf of buffers) {
        view.setUint32(offset, buf.byteLength); // big-endian by default
        offset += 4;
        message.set(new Uint8Array(buf), offset);
        offset += buf.byteLength;
      }
      this.socket!.send(message);
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
