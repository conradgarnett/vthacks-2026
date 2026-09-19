/**
 * ScentGuard rules. Every threshold here is ILLUSTRATIVE: chosen to make the escalation behaviour
 * demonstrable, NOT derived from any safety standard or medical guidance. See docs/SCENTGUARD_RULES.md,
 * which a test keeps in sync with this table.
 */

export type ReadingKind = 'smoke' | 'co' | 'voc' | 'pm25' | 'aqi';
export type Scope = 'building' | 'regional';

export interface Rule {
  id: string;
  kind: ReadingKind;
  scope: Scope;
  /** Reading value at or above which the rule fires. */
  min: number;
  unit: string;
  level: 1 | 2 | 3 | 4;
  meaning: string;
}

export const RULES: readonly Rule[] = [
  { id: 'S1', kind: 'smoke', scope: 'building', min: 0.5, unit: '%obs/m', level: 1, meaning: 'Smoke reading above background' },
  { id: 'S2', kind: 'smoke', scope: 'building', min: 2, unit: '%obs/m', level: 2, meaning: 'Smoke reading clearly elevated' },
  { id: 'S3', kind: 'smoke', scope: 'building', min: 5, unit: '%obs/m', level: 3, meaning: 'Smoke reading high' },
  { id: 'S4', kind: 'smoke', scope: 'building', min: 10, unit: '%obs/m', level: 4, meaning: 'Smoke reading very high' },
  { id: 'C2', kind: 'co', scope: 'building', min: 10, unit: 'ppm', level: 2, meaning: 'Carbon monoxide elevated' },
  { id: 'C3', kind: 'co', scope: 'building', min: 35, unit: 'ppm', level: 3, meaning: 'Carbon monoxide high' },
  { id: 'C4', kind: 'co', scope: 'building', min: 100, unit: 'ppm', level: 4, meaning: 'Carbon monoxide very high' },
  { id: 'V1', kind: 'voc', scope: 'building', min: 500, unit: 'ppb', level: 1, meaning: 'Volatile organic compounds elevated' },
  { id: 'V2', kind: 'voc', scope: 'building', min: 2000, unit: 'ppb', level: 2, meaning: 'Volatile organic compounds high' },
  { id: 'A1', kind: 'aqi', scope: 'regional', min: 100, unit: 'AQI', level: 1, meaning: 'Regional air quality index elevated' },
  { id: 'A2', kind: 'aqi', scope: 'regional', min: 150, unit: 'AQI', level: 2, meaning: 'Regional air quality index high' },
  { id: 'P1', kind: 'pm25', scope: 'regional', min: 35, unit: 'ug/m3', level: 1, meaning: 'Regional fine particles elevated' },
  { id: 'P2', kind: 'pm25', scope: 'regional', min: 55, unit: 'ug/m3', level: 2, meaning: 'Regional fine particles high' },
];

/** Combination rules that use more than one input. */
export const COMBINATION_RULES = [
  { id: 'X1', level: 3, text: 'A fire alarm is ACTIVE from a VERIFIED source: at least level 3, even without a smoke reading.' },
  {
    id: 'X2',
    level: 4,
    text: 'A fire alarm is ACTIVE from a VERIFIED source AND a building smoke reading is at least the S3 threshold: level 4.',
  },
] as const;

/** Regional data alone can raise the level to at most this: it does not describe conditions inside a building. */
export const REGIONAL_LEVEL_CAP = 2;

export const LEVEL_LABEL = ['No elevated reading reported', 'Elevated', 'High', 'Severe', 'Critical'] as const;
