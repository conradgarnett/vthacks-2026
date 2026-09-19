import type { SoundEvent, SoundLabel } from '@sense/providers';

export interface ScriptedSound {
  atSec: number;
  label: SoundLabel;
  confidence: number;
  bearingDeg?: number;
}

/** A simulated soundscape for demos: events fire when the timeline reaches them. Labelled "scripted". */
export class ScriptedSoundscape {
  readonly name = 'scripted';
  private next = 0;

  constructor(private readonly script: ScriptedSound[]) {
    this.script = [...script].sort((a, b) => a.atSec - b.atSec);
  }

  /** Events due at or before `elapsedSec`, each returned once. */
  due(elapsedSec: number): SoundEvent[] {
    const out: SoundEvent[] = [];
    while (this.next < this.script.length && (this.script[this.next] as ScriptedSound).atSec <= elapsedSec) {
      const s = this.script[this.next++] as ScriptedSound;
      out.push({
        label: s.label,
        confidence: s.confidence,
        ...(s.bearingDeg !== undefined ? { bearingDeg: s.bearingDeg } : {}),
        directionKnown: s.bearingDeg !== undefined,
        provider: this.name,
        at: s.atSec,
      });
    }
    return out;
  }

  reset(): void {
    this.next = 0;
  }
}

export const DEMO_SOUNDSCAPE: ScriptedSound[] = [
  { atSec: 0, label: 'knock', confidence: 0.72, bearingDeg: 350 },
  { atSec: 2, label: 'dog-bark', confidence: 0.6, bearingDeg: 120 },
  { atSec: 4, label: 'siren', confidence: 0.78, bearingDeg: 270 },
  { atSec: 6, label: 'vehicle', confidence: 0.5 },
];
