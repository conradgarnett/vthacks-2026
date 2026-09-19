/** Sound labels SENSE can report. "unknown" is always allowed: SENSE would rather say so than guess. */
export const SOUND_LABELS = ['siren', 'knock', 'doorbell', 'alarm', 'speech', 'vehicle', 'dog-bark', 'glass-break', 'unknown'] as const;
export type SoundLabel = (typeof SOUND_LABELS)[number];

export interface SoundEvent {
  label: SoundLabel;
  /** 0..1. Always shown to the user: a sound label is an inference. */
  confidence: number;
  /** Bearing clockwise from straight ahead, only when the input allows (stereo). */
  bearingDeg?: number;
  directionKnown: boolean;
  /** Which provider produced it, e.g. "local-heuristic" or "scripted". */
  provider: string;
  /** Seconds from the start of the stream. */
  at: number;
}

/** Pluggable classifier. A proper vendored model can implement this without touching the rest. */
export interface AudioClassifier {
  readonly name: string;
  /** Classify one mono or stereo window of samples. Returns zero or more events. */
  classify(input: { left: Float32Array; right?: Float32Array; sampleRate: number; at: number }): SoundEvent[];
}
