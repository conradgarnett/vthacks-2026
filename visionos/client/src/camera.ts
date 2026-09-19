/**
 * Rear camera capture with downscaling.
 *
 * Frames are compressed hard (640px wide, JPEG q0.6) for the fast path: the
 * detector doesn't need more, and bytes on a congested hotspot are latency.
 * Hi-res capture is a separate call, used only for reading text.
 */

const FAST_WIDTH = 640;
const FAST_QUALITY = 0.6;
// Reading is resolution-bound: OCR needs the strokes, and text photographed
// from across a room occupies very few pixels. Worth the extra bytes on a
// once-per-request capture, unlike the per-frame fast path.
const HIRES_WIDTH = 1600;
const HIRES_QUALITY = 0.9;

export class Camera {
  private video: HTMLVideoElement;
  private canvas = document.createElement("canvas");
  private stream: MediaStream | null = null;

  constructor(video: HTMLVideoElement) {
    this.video = video;
  }

  /** Throws with a human-readable reason; the caller speaks it aloud. */
  async start(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(
        "This browser can't open the camera. Make sure you opened the page over https."
      );
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
    } catch (err) {
      const name = (err as DOMException)?.name;
      if (name === "NotAllowedError") {
        throw new Error(
          "Camera permission was denied. Allow camera access and reload."
        );
      }
      if (name === "NotFoundError") {
        throw new Error("No camera found on this device.");
      }
      throw new Error("The camera failed to start.");
    }

    this.video.srcObject = this.stream;
    this.video.setAttribute("playsinline", "true");
    this.video.muted = true;
    await this.video.play();
  }

  get isRunning(): boolean {
    return this.stream !== null && this.video.videoWidth > 0;
  }

  captureFast(): Promise<Blob | null> {
    return this.capture(FAST_WIDTH, FAST_QUALITY);
  }

  /** Higher resolution, for OCR only -- costs bytes and time. */
  captureDetailed(): Promise<Blob | null> {
    return this.capture(HIRES_WIDTH, HIRES_QUALITY);
  }

  private capture(targetWidth: number, quality: number): Promise<Blob | null> {
    const { videoWidth, videoHeight } = this.video;
    if (!videoWidth || !videoHeight) return Promise.resolve(null);

    const scale = Math.min(1, targetWidth / videoWidth);
    this.canvas.width = Math.round(videoWidth * scale);
    this.canvas.height = Math.round(videoHeight * scale);

    const ctx = this.canvas.getContext("2d");
    if (!ctx) return Promise.resolve(null);
    ctx.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);

    return new Promise((resolve) =>
      this.canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality)
    );
  }

  stop(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }
}
