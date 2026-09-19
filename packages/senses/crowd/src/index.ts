import { guardPercept, iso, seededRandom, systemClock, type Clock, type Percept, type SenseModule } from '@sense/protocol';

/**
 * Crowd density: a NEW sense added as a plugin. It changes nothing in the core: it only implements
 * `SenseModule` and produces ordinary percepts, so trust tiers, safety guards, routing and
 * rendering for every persona work automatically. It imports only @sense/protocol.
 */
export interface CrowdReading {
  zone: string;
  /** 0 (empty) .. 1 (packed). */
  density: number;
}

const LEVELS = [
  { max: 0.3, word: 'quiet', urgency: 0 },
  { max: 0.6, word: 'busy', urgency: 1 },
  { max: 0.85, word: 'crowded', urgency: 1 },
  { max: 1.01, word: 'very crowded', urgency: 2 },
] as const;

export function levelFor(density: number): (typeof LEVELS)[number] {
  return LEVELS.find((l) => density < l.max) ?? LEVELS[LEVELS.length - 1]!;
}

/** Deterministic simulated crowd sensor: the density drifts up and down around a zone. */
export async function* simulatedCrowd(seed = 1, readings = 6, zone = 'Main hall'): AsyncIterable<CrowdReading> {
  const rnd = seededRandom(seed);
  let density = 0.2;
  for (let i = 0; i < readings; i++) {
    density = Math.min(1, Math.max(0, density + (rnd() - 0.35) * 0.4));
    yield { zone, density: Math.round(density * 100) / 100 };
  }
}

export class CrowdSense implements SenseModule {
  readonly id = 'crowd-density';
  readonly inputs = ['sim:crowd-sensor'];
  private n = 0;

  constructor(
    private readonly readings: () => AsyncIterable<CrowdReading>,
    private readonly clock: Clock = systemClock,
  ) {}

  async *produce(): AsyncIterable<Percept> {
    let last = '';
    for await (const r of this.readings()) {
      const level = levelFor(r.density);
      if (level.word === last) continue; // only say something when the level changes
      last = level.word;
      yield guardPercept({
        id: `crowd-${++this.n}`,
        timestamp: iso(this.clock),
        sense: 'vision',
        kind: 'status',
        urgency: level.urgency,
        short: `${r.zone} is ${level.word}. Inferred, simulated sensor.`,
        long: `${r.zone} crowd density ${Math.round(r.density * 100)}%. This comes from a simulated crowd sensor and is an estimate, not a count of people.`,
        provenance: {
          tier: 'INFERRED',
          source: 'sim-crowd-sensor',
          sourceLabel: 'simulated sensor',
          confidence: 0.6,
          evidence: ['simulated crowd sensor', `density ${r.density}`],
        },
        simulated: true,
      });
    }
  }
}
