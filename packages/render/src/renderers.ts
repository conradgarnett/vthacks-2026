import type { Percept, SensoryProfile } from '@sense/protocol';
import { earconSchedule } from './earcon';
import { route, type RenderPlan } from './route';

/**
 * Browser renderers behind small injectable environments so they can be unit-tested and so the
 * app degrades honestly when an API is missing (captions for speech, a visual flash for haptics).
 */

// ── Speech (Web Speech API) ───────────────────────────────────────────────────────────────────

export interface UtteranceLike {
  text: string;
  rate: number;
  lang: string;
  onend?: (() => void) | null;
}

export interface SpeechEnv {
  synth?: { speak(u: UtteranceLike): void; cancel(): void };
  createUtterance?: (text: string) => UtteranceLike;
}

export function browserSpeechEnv(): SpeechEnv {
  const g = globalThis as unknown as {
    speechSynthesis?: SpeechEnv['synth'];
    SpeechSynthesisUtterance?: new (text: string) => UtteranceLike;
  };
  const Ctor = g.SpeechSynthesisUtterance;
  return {
    ...(g.speechSynthesis ? { synth: g.speechSynthesis } : {}),
    ...(Ctor ? { createUtterance: (t: string) => new Ctor(t) } : {}),
  };
}

export class SpeechRenderer {
  constructor(private readonly env: SpeechEnv = browserSpeechEnv()) {}

  get available(): boolean {
    return Boolean(this.env.synth && this.env.createUtterance);
  }

  /** Speak, or report 'captions-only' (the caption is always shown by the UI regardless). */
  speak(speech: NonNullable<RenderPlan['speech']>, lang = 'en'): 'spoken' | 'captions-only' {
    const { synth, createUtterance } = this.env;
    if (!synth || !createUtterance) return 'captions-only';
    if (speech.interrupt) synth.cancel();
    const u = createUtterance(speech.text);
    u.rate = speech.rate;
    u.lang = lang;
    synth.speak(u);
    return 'spoken';
  }

  cancel(): void {
    this.env.synth?.cancel();
  }
}

// ── Spatial audio (Web Audio) ─────────────────────────────────────────────────────────────────

export interface AudioEnv {
  createContext?: () => AudioContext | undefined;
}

export function browserAudioEnv(): AudioEnv {
  return {
    createContext: () => {
      const g = globalThis as unknown as { AudioContext?: new () => AudioContext };
      return g.AudioContext ? new g.AudioContext() : undefined;
    },
  };
}

export class SpatialAudioRenderer {
  private ctx: AudioContext | undefined;

  constructor(private readonly env: AudioEnv = browserAudioEnv()) {}

  /** Play an earcon positioned with an HRTF PannerNode. Returns 'unavailable' without Web Audio. */
  play(plan: NonNullable<RenderPlan['spatialAudio']>): 'played' | 'unavailable' {
    this.ctx ??= this.env.createContext?.();
    const ctx = this.ctx;
    if (!ctx) return 'unavailable';
    void ctx.resume?.();
    const panner = ctx.createPanner();
    panner.panningModel = 'HRTF';
    panner.positionX.value = plan.position.x;
    panner.positionY.value = plan.position.y;
    panner.positionZ.value = plan.position.z;
    panner.connect(ctx.destination);
    const t0 = ctx.currentTime;
    for (const note of earconSchedule(plan.urgency)) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = note.wave;
      osc.frequency.value = note.freq;
      // A sound behind the listener is a little duller and quieter, as a real one would be.
      const g = note.gain * (plan.behind ? 0.8 : 1);
      gain.gain.setValueAtTime(0, t0 + note.start);
      gain.gain.linearRampToValueAtTime(g, t0 + note.start + 0.01);
      gain.gain.linearRampToValueAtTime(0, t0 + note.start + note.duration);
      osc.connect(gain);
      gain.connect(panner);
      osc.start(t0 + note.start);
      osc.stop(t0 + note.start + note.duration + 0.02);
    }
    return 'played';
  }

  close(): void {
    void this.ctx?.close?.();
    this.ctx = undefined;
  }
}

// ── Haptics (Vibration API) ───────────────────────────────────────────────────────────────────

export interface HapticEnv {
  vibrate?: (pattern: number[]) => boolean;
}

export function browserHapticEnv(): HapticEnv {
  const nav = (globalThis as unknown as { navigator?: { vibrate?: (p: number[]) => boolean } }).navigator;
  return nav?.vibrate ? { vibrate: (p) => nav.vibrate?.(p) ?? false } : {};
}

export class HapticRenderer {
  constructor(private readonly env: HapticEnv = browserHapticEnv()) {}

  /** 'unsupported' means the UI must show the visual equivalent (a flash plus the text pattern). */
  fire(haptic: NonNullable<RenderPlan['haptic']>): 'vibrated' | 'unsupported' {
    if (!this.env.vibrate || haptic.pattern.length === 0) return 'unsupported';
    return this.env.vibrate(haptic.pattern) ? 'vibrated' : 'unsupported';
  }
}

// ── Presenter ─────────────────────────────────────────────────────────────────────────────────

export interface PresentResult {
  plan: RenderPlan;
  speech?: 'spoken' | 'captions-only';
  audio?: 'played' | 'unavailable';
  haptic?: 'vibrated' | 'unsupported';
}

/** Drives all renderers for a percept. The visual card and captions are rendered by the UI. */
export class Presenter {
  constructor(
    private readonly speech = new SpeechRenderer(),
    private readonly audio = new SpatialAudioRenderer(),
    private readonly haptics = new HapticRenderer(),
  ) {}

  present(percept: Percept, profile: SensoryProfile): PresentResult {
    const plan = route(percept, profile);
    const out: PresentResult = { plan };
    if (plan.speech) out.speech = this.speech.speak(plan.speech, profile.language);
    if (plan.spatialAudio) out.audio = this.audio.play(plan.spatialAudio);
    if (plan.haptic) out.haptic = this.haptics.fire(plan.haptic);
    return out;
  }
}
