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
 *
 * Focus is the other thing a webcam gets wrong. A lens that can focus is put
 * in continuous autofocus and asked to settle again right before a read; a
 * lens that cannot is reported as such, so the user can be told to hold a
 * label a hand's length away instead of against the glasses.
 */

const FAST_WIDTH = 640;
const FAST_QUALITY = 0.6;
const HIRES_WIDTH = 1920;
const HIRES_QUALITY = 0.85;
// A background peek: enough pixels for a label, a fraction of a read's bytes.
const PEEK_WIDTH = 1280;
const PEEK_QUALITY = 0.75;
// A scan frame: the whole field of view at a size the detector can see a
// chair at the end of a hallway in.
const SCAN_WIDTH = 1280;
const SCAN_QUALITY = 0.8;

/**
 * The part of the frame a read looks at: the middle two thirds, drawn on
 * screen as the read area so a sighted helper sees exactly what the reader
 * sees. Scans and questions use the whole field of view; reading is aimed,
 * and a label held up is in the middle of it, while text at the edges is
 * someone else's sign. Fractions of the frame, origin top left.
 */
export const READ_WINDOW = { x: 1 / 6, y: 1 / 6, width: 2 / 3, height: 2 / 3 };
type Window = typeof READ_WINDOW;

/** Where a window sits on the page, in CSS pixels. */
export type ScreenRect = { left: number; top: number; width: number; height: number };
// Time for a single-shot autofocus to settle before the burst is captured.
const REFOCUS_MS = 600;
// Time for the sensor to settle after exposure is turned down for a read.
const EXPOSURE_SETTLE_MS = 300;
// The dim for a read changes the webcam's own controls, which outlive the
// page. If the page is reloaded mid-read, or the restore fails, the camera
// stays dim and the next session takes that as normal. The values from
// before the dim are kept here until they have been put back.
const EXPOSURE_KEY = "visionos.exposureBefore";
// How far down the picture goes for a read, as a fraction of each control's
// range. A webcam's automatic exposure blows a glossy label out to white,
// and the ink is what the reader needs; a quarter of the range is a stop
// or so, dark enough to keep the whites and not so dark that shadows go.
const READ_EXPOSURE_DROP = 0.25;
const READ_BRIGHTNESS_DROP = 0.2;
// Contrast barely moves: a real raise made the picture look like a different
// camera. Sharpness goes up for the strokes.
const READ_CONTRAST_RAISE = 0.03;
const READ_SHARPNESS_RAISE = 0.25;

// Cameras that live in the machine rather than on the user.
const BUILT_IN = /integrated|built-?in|facetime|internal|easycamera|true ?vision|wide ?vision|user.facing|front/i;
// Cameras that plug in, by the names they tend to carry.
const EXTERNAL = /usb|logitech|brio|uvc|razer|elgato|obsbot|insta360|external|kiyo|c9\d\d/i;
// USB vendor:product ids that browsers append to a webcam's name.
const VENDOR_ID = /\s*\([0-9a-f]{4}:[0-9a-f]{4}\)/i;

// Focus and exposure controls are not in the TypeScript DOM typings yet;
// browsers expose them on cameras whose driver offers them.
type FocusCapabilities = { focusMode?: string[] };
type FocusConstraint = { focusMode: string };
type Range = { min: number; max: number; step?: number };
type ImageCapabilities = {
  exposureCompensation?: Range;
  brightness?: Range;
  contrast?: Range;
  saturation?: Range;
  sharpness?: Range;
  exposureMode?: string[];
  whiteBalanceMode?: string[];
};
type ImageSettings = Record<string, number | string | undefined>;
// The picture controls a reset puts back to the middle of their range,
// which is where webcams ship.
const LEVEL_CONTROLS: ReadonlyArray<"exposureCompensation" | "brightness" | "contrast" | "saturation" | "sharpness"> = [
  "exposureCompensation",
  "brightness",
  "contrast",
  "saturation",
  "sharpness",
];

/** The middle of a control's range, on its step grid. */
function midpoint(range: Range): number {
  const middle = (range.min + range.max) / 2;
  if (!range.step || range.step <= 0) return middle;
  return range.min + Math.round((middle - range.min) / range.step) * range.step;
}
const EXPOSURE_CONTROLS: Array<[(typeof LEVEL_CONTROLS)[number], number]> = [
  ["exposureCompensation", -READ_EXPOSURE_DROP],
  ["brightness", -READ_BRIGHTNESS_DROP],
  ["contrast", READ_CONTRAST_RAISE],
  ["sharpness", READ_SHARPNESS_RAISE],
];

export class Camera {
  private video: HTMLVideoElement;
  private canvas = document.createElement("canvas");
  private stream: MediaStream | null = null;
  private cameras: MediaDeviceInfo[] = [];
  /** "autofocus" or "no focus control", once started; spoken with the name. */
  focus = "";
  /** Exposure settings to put back after a read, while one is dimmed. */
  private exposureBefore: Record<string, number> | null = null;

  constructor(video: HTMLVideoElement) {
    this.video = video;
  }

  /**
   * Throws with a human-readable reason; the caller speaks it aloud.
   * `preferred` is part of a camera's name, from the page URL.
   */
  async start(preferred: string | null = null, exposure: string | null = null): Promise<void> {
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
    await this.resetImageControls(exposure);
    // Best effort on the way out: a reload during a read must not leave the
    // camera dim for the next session.
    window.addEventListener("pagehide", () => void this.restoreExposure());
  }

  /**
   * Put every picture control the camera offers back where a webcam ships:
   * automatic exposure and white balance, and the middle of the range for
   * exposure compensation, brightness, contrast, saturation and sharpness.
   *
   * Done at every start. The read-time dim writes the camera's own
   * controls, which outlive the page, so a reload mid-read, a crash, or
   * another program's leftovers can leave the picture dark or off-colour,
   * and the user cannot see that. Restoring only the values remembered
   * from before a dim was not enough: a session that started on an
   * already-dim camera remembered the dim as normal. `?exposure=reset` is
   * still accepted and means the same thing.
   */
  private async resetImageControls(_exposure: string | null): Promise<void> {
    const track = this.track();
    if (!track) return;
    const capabilities = (track.getCapabilities?.() ?? {}) as ImageCapabilities;
    const values: Record<string, number | string> = {};
    if (capabilities.exposureMode?.includes("continuous")) values.exposureMode = "continuous";
    if (capabilities.whiteBalanceMode?.includes("continuous")) values.whiteBalanceMode = "continuous";
    for (const name of LEVEL_CONTROLS) {
      const range = capabilities[name];
      if (range && range.max > range.min) values[name] = midpoint(range);
    }
    if (Object.keys(values).length === 0) return;
    const applied = await this.applyEach(track, values);
    console.info(`[camera] picture controls reset: ${applied} of ${Object.keys(values).length}`, values);
    this.exposureBefore = null;
    this.forgetExposure();
  }

  /**
   * Apply controls one at a time. In one `advanced` set the browser drops
   * the whole set when any single control is refused, so a camera that
   * lacked one of them silently kept every other one as it was.
   */
  private async applyEach(track: MediaStreamTrack, values: Record<string, number | string>): Promise<number> {
    let applied = 0;
    for (const [name, value] of Object.entries(values)) {
      try {
        await track.applyConstraints({ advanced: [{ [name]: value } as unknown as MediaTrackConstraintSet] });
        applied += 1;
      } catch {
        // This camera does not take this control; the rest still go.
      }
    }
    return applied;
  }

  private storedExposure(): Record<string, number> | null {
    try {
      const raw = localStorage.getItem(EXPOSURE_KEY);
      return raw ? (JSON.parse(raw) as Record<string, number>) : null;
    } catch {
      return null;
    }
  }

  private forgetExposure(): void {
    try {
      localStorage.removeItem(EXPOSURE_KEY);
    } catch {
      // Private browsing or blocked storage.
    }
  }

  /** The camera in use, named for speech; empty before start. */
  get label(): string {
    const raw = this.track()?.label ?? "";
    return raw.replace(VENDOR_ID, "").trim() || "camera";
  }

  /**
   * Ask an autofocus lens to settle on whatever is in front of it now.
   * Continuous autofocus hunts, and a burst captured mid-hunt is three
   * soft frames. Does nothing on a lens without focus control.
   */
  async refocus(): Promise<void> {
    const track = this.track();
    const modes = this.focusModes(track);
    if (!track || !modes.includes("single-shot")) return;
    try {
      await this.setFocus(track, "single-shot");
      await new Promise((resolve) => setTimeout(resolve, REFOCUS_MS));
      if (modes.includes("continuous")) await this.setFocus(track, "continuous");
    } catch {
      // Focus is best effort; the read goes ahead with what the lens gives.
    }
  }

  /**
   * Turn the picture down for a read. A webcam's automatic exposure blows
   * a glossy label out to white, and the ink is what the reader needs.
   * Uses whichever of exposure compensation, brightness, contrast and
   * sharpness the camera offers; does nothing on one that offers none. Put back with
   * `restoreExposure`, so the preview and the scan frames are untouched.
   */
  async dimForRead(): Promise<void> {
    const track = this.track();
    if (!track || this.exposureBefore) return;
    const capabilities = (track.getCapabilities?.() ?? {}) as ImageCapabilities;
    const settings = (track.getSettings?.() ?? {}) as ImageSettings;

    const before: Record<string, number> = {};
    const dimmed: Record<string, number> = {};
    for (const [name, change] of EXPOSURE_CONTROLS) {
      const range = capabilities[name];
      if (!range || !(range.max > range.min)) continue;
      const current = typeof settings[name] === "number" ? (settings[name] as number) : (range.min + range.max) / 2;
      const step = range.step && range.step > 0 ? range.step : 0;
      let target = current + (range.max - range.min) * change;
      target = Math.min(range.max, Math.max(range.min, target));
      if (step) target = range.min + Math.round((target - range.min) / step) * step;
      if (target === current) continue;
      before[name] = current;
      dimmed[name] = target;
    }
    if (Object.keys(dimmed).length === 0) return;

    try {
      if ((await this.applyEach(track, dimmed)) === 0) throw new Error("no control accepted");
      this.exposureBefore = before;
      try {
        localStorage.setItem(EXPOSURE_KEY, JSON.stringify(before));
      } catch {
        // Private browsing or blocked storage: the in-memory copy still works.
      }
      await new Promise((resolve) => setTimeout(resolve, EXPOSURE_SETTLE_MS));
    } catch {
      // The camera refused; the read goes ahead with the picture as it is.
      this.exposureBefore = null;
    }
  }

  /** Put the exposure back the way it was before `dimForRead`. */
  async restoreExposure(): Promise<void> {
    const track = this.track();
    const before = this.exposureBefore ?? this.storedExposure();
    this.exposureBefore = null;
    if (!track || !before) return;
    if ((await this.applyEach(track, before)) > 0) this.forgetExposure();
    // Otherwise the stored values are kept; the next start resets the
    // camera anyway.
  }

  private track(): MediaStreamTrack | undefined {
    return this.stream?.getVideoTracks()[0];
  }

  private focusModes(track: MediaStreamTrack | undefined): string[] {
    const capabilities = (track?.getCapabilities?.() ?? {}) as FocusCapabilities;
    return capabilities.focusMode ?? [];
  }

  private setFocus(track: MediaStreamTrack, mode: string): Promise<void> {
    const constraint = { focusMode: mode } as FocusConstraint as unknown as MediaTrackConstraintSet;
    return track.applyConstraints({ advanced: [constraint] });
  }

  private async applyFocus(): Promise<void> {
    const track = this.track();
    const modes = this.focusModes(track);
    if (track && modes.includes("continuous")) {
      this.focus = "autofocus";
      try {
        await this.setFocus(track, "continuous");
      } catch {
        // The lens keeps whatever mode it had.
      }
    } else {
      this.focus = modes.length ? "manual focus" : "no focus control";
    }
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
  }

  private async show(): Promise<void> {
    await this.applyFocus();
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
    return this.track()?.getSettings().deviceId;
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

    // A phone's rear camera already faces the world; leave it alone. A
    // computer's own camera faces the user, or reports nothing, and either
    // way the one worn on the glasses is somewhere else in the list.
    const facing = this.track()?.getSettings().facingMode;
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

  /** The whole view at scan size, for describing the scene. */
  captureScan(): Promise<Blob | null> {
    return this.capture(SCAN_WIDTH, SCAN_QUALITY);
  }

  /** Middling resolution of the read area, for the background reader. */
  capturePeek(): Promise<Blob | null> {
    return this.capture(PEEK_WIDTH, PEEK_QUALITY, READ_WINDOW);
  }

  /** Full resolution of the read area, for reading text on request. */
  captureDetailed(): Promise<Blob | null> {
    return this.capture(HIRES_WIDTH, HIRES_QUALITY, READ_WINDOW);
  }

  /**
   * Where the read area sits on the page, so it can be drawn over the
   * preview. Accounts for the letterboxing of a preview that shows the
   * whole frame. Null until the camera has a size.
   */
  readWindowOnScreen(): ScreenRect | null {
    const frame = this.frameOnScreen();
    if (!frame) return null;
    return {
      left: frame.left + frame.width * READ_WINDOW.x,
      top: frame.top + frame.height * READ_WINDOW.y,
      width: frame.width * READ_WINDOW.width,
      height: frame.height * READ_WINDOW.height,
    };
  }

  /** Where the whole frame sits on the page, letterboxing included. */
  frameOnScreen(): ScreenRect | null {
    const { videoWidth, videoHeight } = this.video;
    if (!videoWidth || !videoHeight) return null;
    const rect = this.video.getBoundingClientRect();
    const scale = Math.min(rect.width / videoWidth, rect.height / videoHeight);
    const width = videoWidth * scale;
    const height = videoHeight * scale;
    return {
      left: rect.left + (rect.width - width) / 2,
      top: rect.top + (rect.height - height) / 2,
      width,
      height,
    };
  }

  private capture(targetWidth: number, quality: number, window?: Window): Promise<Blob | null> {
    const { videoWidth, videoHeight } = this.video;
    if (!videoWidth || !videoHeight) return Promise.resolve(null);

    // The source rectangle: the whole frame, or the read area of it.
    const sx = window ? Math.round(videoWidth * window.x) : 0;
    const sy = window ? Math.round(videoHeight * window.y) : 0;
    const sw = window ? Math.round(videoWidth * window.width) : videoWidth;
    const sh = window ? Math.round(videoHeight * window.height) : videoHeight;

    const scale = Math.min(1, targetWidth / sw);
    this.canvas.width = Math.round(sw * scale);
    this.canvas.height = Math.round(sh * scale);

    const ctx = this.canvas.getContext("2d");
    if (!ctx) return Promise.resolve(null);
    ctx.drawImage(this.video, sx, sy, sw, sh, 0, 0, this.canvas.width, this.canvas.height);

    return new Promise((resolve) =>
      this.canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality)
    );
  }
}
