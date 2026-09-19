/**
 * Camera capture with downscaling, and the choice of which camera.
 *
 * The stream is requested at 1080p so a read has real pixels to work with:
 * OCR needs the strokes, and text photographed from across a room occupies
 * very few of them. The fast path downscales every frame to 640 px, JPEG
 * q0.6, because the detector needs no more and bytes on a congested hotspot
 * are latency. Full resolution is captured only on request, for reading.
 *
 * Which camera matters as much as how it is read. On a phone the rear camera
 * is the one pointed at the world. On a laptop wearing a webcam on a pair of
 * glasses, the camera above the screen is the wrong one and `facingMode`
 * says nothing useful, so an external camera is preferred whenever there is
 * one, and `?camera=<part of its name>` pins a camera by name. The caller
 * speaks the choice: a wrong camera is invisible to someone who cannot see
 * the preview.
 */

const FAST_WIDTH = 640;
const FAST_QUALITY = 0.6;
const HIRES_WIDTH = 1920;
const HIRES_QUALITY = 0.85;

// Cameras that live in the machine rather than on the user.
const BUILT_IN = /integrated|built-?in|facetime|internal/i;
// A phone names its cameras by where they point; a computer never does.
const PHONE_CAMERA = /\b(back|rear|front)\b/i;
// USB vendor:product ids that browsers append to a webcam's name.
const VENDOR_ID = /\s*\([0-9a-f]{4}:[0-9a-f]{4}\)/i;

export class Camera {
  private video: HTMLVideoElement;
  private canvas = document.createElement("canvas");
  private stream: MediaStream | null = null;
  /** How many cameras the browser offered, once started. */
  count = 0;

  constructor(video: HTMLVideoElement) {
    this.video = video;
  }

  /**
   * Throws with a human-readable reason; the caller speaks it aloud.
   * `preferred` is part of a camera's name, from the page URL.
   */
  async start(preferred: string | null = null): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(
        "This browser can't open the camera. Make sure you opened the page over https."
      );
    }

    // Permission first: camera names are blank until it has been granted.
    this.stream = await this.open({ facingMode: { ideal: "environment" } });

    const chosen = await this.choose(preferred);
    if (chosen && chosen.deviceId !== this.currentDeviceId()) {
      const fallback = this.stream;
      try {
        this.stream = await this.open({ deviceId: { exact: chosen.deviceId } });
        fallback.getTracks().forEach((track) => track.stop());
      } catch {
        // A working camera beats the preferred one that would not open.
        this.stream = fallback;
      }
    }

    this.video.srcObject = this.stream;
    this.video.setAttribute("playsinline", "true");
    this.video.muted = true;
    await this.video.play();
  }

  /** The camera in use, named for speech; empty before start. */
  get label(): string {
    const raw = this.stream?.getVideoTracks()[0]?.label ?? "";
    return raw.replace(VENDOR_ID, "").trim();
  }

  private async open(video: MediaTrackConstraints): Promise<MediaStream> {
    try {
      return await navigator.mediaDevices.getUserMedia({
        video: { ...video, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
    } catch (err) {
      const name = (err as DOMException)?.name;
      if (name === "NotAllowedError") {
        throw new Error("Camera permission was denied. Allow camera access and reload.");
      }
      if (name === "NotFoundError") {
        throw new Error("No camera found on this device.");
      }
      throw new Error("The camera failed to start.");
    }
  }

  private currentDeviceId(): string | undefined {
    return this.stream?.getVideoTracks()[0]?.getSettings().deviceId;
  }

  /** The camera to switch to, or null to keep the browser's choice. */
  private async choose(preferred: string | null): Promise<MediaDeviceInfo | null> {
    let cameras: MediaDeviceInfo[] = [];
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      cameras = devices.filter((device) => device.kind === "videoinput");
    } catch {
      return null;
    }
    this.count = cameras.length;

    if (preferred) {
      const wanted = preferred.toLowerCase();
      const match = cameras.find((camera) => camera.label.toLowerCase().includes(wanted));
      if (match) return match;
    }

    // A phone's rear camera already faces the world. Only a computer needs
    // steering away from the camera above its own screen, and only when
    // there is somewhere else to steer to.
    const settings = this.stream?.getVideoTracks()[0]?.getSettings();
    if (settings?.facingMode || cameras.some((camera) => PHONE_CAMERA.test(camera.label))) {
      return null;
    }
    if (cameras.length < 2) return null;
    return cameras.find((camera) => camera.label && !BUILT_IN.test(camera.label)) ?? null;
  }

  get isRunning(): boolean {
    return this.stream !== null && this.video.videoWidth > 0;
  }

  captureFast(): Promise<Blob | null> {
    return this.capture(FAST_WIDTH, FAST_QUALITY);
  }

  /** Full resolution, for reading text. Costs bytes and time; use on request. */
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
