/**
 * 3D spatial audio: sound that arrives from where the object actually is.
 *
 * This is the part a description cannot replace. "The door is at your two
 * o'clock" requires the listener to translate words into a direction; a ping
 * that genuinely comes from their right does not.
 *
 * Tones are synthesized rather than loaded: no asset fetch, so audio works on
 * a dead network, and pitch and length become parameters instead of files.
 */

export type Earcon = "hazard" | "beacon" | "info" | "found";

type ToneSpec = {
  frequency: number;
  duration: number;
  type: OscillatorType;
  gain: number;
  /** Ratio to sweep toward over the tone's life. 1 = steady. */
  sweep?: number;
};

// Timbres are deliberately distinct: a user learns these by sound alone, and
// two similar earcons are worse than one.
const TONES: Record<Earcon, ToneSpec> = {
  hazard: { frequency: 880, duration: 0.17, type: "square", gain: 0.5, sweep: 0.55 },
  beacon: { frequency: 660, duration: 0.09, type: "sine", gain: 0.3 },
  info: { frequency: 520, duration: 0.13, type: "sine", gain: 0.22 },
  found: { frequency: 780, duration: 0.2, type: "triangle", gain: 0.28, sweep: 1.5 },
};

const MIN_BEACON_INTERVAL_MS = 130;
const MAX_BEACON_INTERVAL_MS = 900;

export class SpatialAudio {
  private ctx: AudioContext | null = null;
  private beaconTimer: number | null = null;
  private beaconTarget: { azimuth: number; distance: number } | null = null;

  /**
   * Must be called from a user gesture; browsers start audio suspended.
   *
   * Never throws. This runs during startup, and an exception here used to
   * abort the whole launch sequence silently, leaving the start screen up
   * with no error. Audio is an enhancement -- the app must run without it.
   */
  init(): void {
    try {
      if (this.ctx) {
        void this.ctx.resume();
        return;
      }
      const Ctor = window.AudioContext ?? (window as any).webkitAudioContext;
      if (!Ctor) return;

      this.ctx = new Ctor();
      this.orientListener();
      void this.ctx.resume();
    } catch (err) {
      console.warn("Spatial audio unavailable:", err);
      this.ctx = null;
    }
  }

  /** Listener faces -z, matching the mapping in positionOf(). */
  private orientListener(): void {
    const listener = this.ctx?.listener as any;
    if (!listener) return;
    try {
      // Modern browsers expose AudioParams; Safari still has the deprecated
      // setOrientation(). Assigning .value on a plain number throws in the
      // strict mode that ES modules always run under.
      if (listener.forwardX && typeof listener.forwardX === "object") {
        listener.forwardX.value = 0;
        listener.forwardY.value = 0;
        listener.forwardZ.value = -1;
        listener.upX.value = 0;
        listener.upY.value = 1;
        listener.upZ.value = 0;
      } else if (typeof listener.setOrientation === "function") {
        listener.setOrientation(0, 0, -1, 0, 1, 0);
      }
    } catch (err) {
      // Default orientation already faces -z, so this is cosmetic.
      console.warn("Could not orient audio listener:", err);
    }
  }

  get isReady(): boolean {
    return this.ctx !== null;
  }

  /**
   * Azimuth and distance to Web Audio coordinates.
   * Right-handed: +x right, -z ahead, which matches the listener above.
   */
  private static positionOf(azimuthDeg: number, distanceM: number) {
    const radians = (azimuthDeg * Math.PI) / 180;
    // Clamped: beyond a few meters HRTF cues stop being informative, and a
    // very distant source is simply inaudible.
    const radius = Math.min(Math.max(distanceM, 0.5), 8);
    return { x: Math.sin(radians) * radius, y: 0, z: -Math.cos(radians) * radius };
  }

  /** Play an earcon from a direction. Azimuth in degrees, negative left. */
  play(earcon: Earcon, azimuthDeg = 0, distanceM = 1.5): void {
    if (!this.ctx) return;
    if (this.ctx.state === "suspended") void this.ctx.resume();

    const spec = TONES[earcon];
    const now = this.ctx.currentTime;
    const { x, y, z } = SpatialAudio.positionOf(azimuthDeg, distanceM);

    const panner = this.ctx.createPanner();
    panner.panningModel = "HRTF"; // the whole point: real directional cues
    panner.distanceModel = "inverse";
    panner.refDistance = 1;
    panner.maxDistance = 10;
    if (panner.positionX) {
      panner.positionX.value = x;
      panner.positionY.value = y;
      panner.positionZ.value = z;
    } else {
      (panner as any).setPosition(x, y, z);
    }

    const oscillator = this.ctx.createOscillator();
    oscillator.type = spec.type;
    oscillator.frequency.setValueAtTime(spec.frequency, now);
    if (spec.sweep && spec.sweep !== 1) {
      oscillator.frequency.exponentialRampToValueAtTime(
        spec.frequency * spec.sweep,
        now + spec.duration
      );
    }

    // Exponential decay to near-zero, never to zero: 0 is invalid for an
    // exponential ramp and silently cancels the envelope.
    const envelope = this.ctx.createGain();
    envelope.gain.setValueAtTime(spec.gain, now);
    envelope.gain.exponentialRampToValueAtTime(0.0001, now + spec.duration);

    oscillator.connect(envelope).connect(panner).connect(this.ctx.destination);
    oscillator.start(now);
    oscillator.stop(now + spec.duration);
  }

  /**
   * Repeating ping toward a target: turn until the sound is centered.
   *
   * The repeat rate encodes alignment -- faster as the target approaches dead
   * ahead -- so the user gets continuous feedback while turning instead of
   * having to re-ask.
   */
  startBeacon(azimuthDeg: number, distanceM: number): void {
    this.stopBeacon();
    this.beaconTarget = { azimuth: azimuthDeg, distance: distanceM };

    const tick = () => {
      if (!this.beaconTarget) return;
      const { azimuth, distance } = this.beaconTarget;
      this.play("beacon", azimuth, distance);

      const alignment = Math.min(Math.abs(azimuth) / 90, 1);
      const interval =
        MIN_BEACON_INTERVAL_MS +
        (MAX_BEACON_INTERVAL_MS - MIN_BEACON_INTERVAL_MS) * alignment;
      this.beaconTimer = window.setTimeout(tick, interval);
    };
    tick();
  }

  /** Update the target as the user turns, without restarting the rhythm. */
  updateBeacon(azimuthDeg: number, distanceM: number): void {
    if (this.beaconTarget) {
      this.beaconTarget = { azimuth: azimuthDeg, distance: distanceM };
    }
  }

  get beaconActive(): boolean {
    return this.beaconTarget !== null;
  }

  stopBeacon(): void {
    if (this.beaconTimer !== null) window.clearTimeout(this.beaconTimer);
    this.beaconTimer = null;
    this.beaconTarget = null;
  }
}
