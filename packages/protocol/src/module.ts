import type { Percept } from './percept';

/**
 * A sense is a plugin: anything that can produce percepts. Adding a new sense ("balance",
 * "temperature", "crowd density") requires no change to the core; see packages/senses/crowd.
 */
export interface SenseModule {
  id: string;
  /** Human-readable inputs, e.g. ["world-sim:crowd-sensor"]. */
  inputs: string[];
  produce(): AsyncIterable<Percept>;
}
