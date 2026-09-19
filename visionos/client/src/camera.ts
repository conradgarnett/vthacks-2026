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
 * glasses, the camera above the screen is the wrong one, and Windows even
 * reports it as facing the "user", so an external camera is preferred
 * whenever there is one, `next()` cycles through the rest, and the last
 * choice is remembered. The caller speaks every choice: a wrong camera is
 * invisible to someone who cannot see the preview.
 */

const FAST_WIDTH = 640;
const FAST_QUALITY = 0.6;
const HIRES_WIDTH = 1920;
const HIRES_QUALITY = 0.85;

// Cameras that live in the machine rather than on the user.
const BUILT_IN = /integrated|built-?in|facetime|internal|easycamera|true ?vision|wide ?vision|user.facing|front/i;
// Cameras that plug in, by the names they tend to carry.
const EXTERNAL = /usb|logitech|brio|uvc|razer|elgato|obsbot|insta360|external|kiyo|c9\d\d/i;
// USB vendor:product ids that browsers append to a webcam's name.
const VENDOR_ID = /\s*\([0-9a-f]{4}:[0-9a-f]{4}\)/i;
const REMEMBERED = "visionos.camera";

export class Camera {
  private video: HTMLVideoElement;
  private canvas = document.createElement("canvas");
  private stream: MediaStream | null = null;
  private cameras: MediaDeviceInfo[] = [];

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
    await this.refreshList();

    const chosen = this.choose(preferred);
    if (chosen && chosen.deviceId !== this.currentDeviceId()) {
      await this.switchTo(chosen);
    }
    await this.show();
  }

  /** Switch to the next camera and return its name. Throws if it fails. */
  async next(): Promise<string> {
    await this.refreshList();
    if (this.cameras.length < 2) {
      throw new Error("This is the only camera.");
    }
    const current = this.cameras.findIndex((camera) => camera.deviceId === this.currentDeviceId());
    const following = this.cameras[(current + 1) % this.cameras.length];
    await this.switchTo(following);
    await this.show();
    return this.label;
  }

  /** How many cameras the browser offered, once started. */
  get count(): number {
    return this.cameras.length;
  }

  /** The camera in use, named for speech; empty before start. */
  get label(): string {
    const raw = this.stream?.getVideoTracks()[0]?.label ?? "";
    return raw.replace(VENDOR_ID, "").trim() || "camera";
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

  private async switchTo(camera: MediaDeviceInfo): Promise<void> {
    const fallback = this.stream;
    this.stream = await this.open({ deviceId: { exact: camera.deviceId } });
    fallback?.getTracks().forEach((track) => track.stop());
    try {
      localStorage.setItem(REMEMBERED, camera.deviceId);
    } catch {
      // Private browsing or blocked storage: the choice just is not kept.
    }
  }

  private async show(): Promise<void> {
    this.video.srcObject = this.stream;
    this.video.setAttribute("playsinline", "true");
    this.video.muted = true;
    await this.video.play();
  }

  private async refreshList(): Promise<void> {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      this.cameras = devices.filter((device) => device.kind === "videoinput");
    } catch {
      this.cameras = [];
    }
  }

  private currentDeviceId(): string | undefined {
    return this.stream?.getVideoTracks()[0]?.getSettings().deviceId;
  }

  /** The camera to switch to, or null to keep the browser's choice. */
  private choose(preferred: string | null): MediaDeviceInfo | null {
    const cameras = this.cameras;
    if (cameras.length < 2) return null;

    if (preferred) {
      const wanted = preferred.toLowerCase();
      const match = cameras.find((camera) => camera.label.toLowerCase().includes(wanted));
      if (match) return match;
    }

    let remembered: string | null = null;
    try {
      remembered = localStorage.getItem(REMEMBERED);
    } catch {
      remembered = null;
    }
    const kept = cameras.find((camera) => camera.deviceId === remembered);
    if (kept) return kept;

    // A phone's rear camera already faces the world; leave it alone. A
    // computer's own camera faces the user, or reports nothing, and either
    // way the one worn on the glasses is somewhere else in the list.
    const facing = this.stream?.getVideoTracks()[0]?.getSettings().facingMode;
    if (facing === "environment") return null;

    return (
      cameras.find((camera) => EXTERNAL.test(camera.label)) ??
      cameras.find((camera) => camera.label && !BUILT_IN.test(camera.label)) ??
      null
    );
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
