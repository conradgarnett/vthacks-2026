import { z } from 'zod';
import { DATA_NOT_INSTRUCTIONS, structured, type LlmClient, type LlmImage } from './llm';

const bearing = z.number().min(0).max(360);
const clock = z.number().int().min(1).max(12);
const distance = z.enum(['near', 'mid', 'far']);
const confidence = z.number().min(0).max(1);
const text = (n: number) => z.string().max(n);

/**
 * Structured scene graph. Positions are relative to the camera: bearing clockwise from straight
 * ahead, clock position, and a coarse distance. There is deliberately NO field for a person's
 * identity, name or face description: people are only counted.
 */
export const SceneGraphSchema = z.strictObject({
  summary: text(300),
  objects: z
    .array(
      z.strictObject({
        label: text(60),
        kind: z.enum(['object', 'door', 'exit', 'obstacle', 'furniture', 'stairs', 'vehicle']),
        bearingDeg: bearing,
        clock,
        distance,
        distanceM: z.number().min(0).max(100).optional(),
        confidence,
      }),
    )
    .max(40),
  signs: z.array(z.strictObject({ text: text(120), bearingDeg: bearing, clock, distance, confidence })).max(10),
  /** How many people are visible. Never who they are. */
  peopleCount: z.number().int().min(0).max(50),
  hazards: z
    .array(
      z.strictObject({
        label: text(60),
        severity: z.enum(['low', 'medium', 'high']),
        bearingDeg: bearing,
        clock,
        distance,
        confidence,
      }),
    )
    .max(10),
  lighting: z.enum(['dark', 'dim', 'normal', 'bright']),
});
export type SceneGraph = z.infer<typeof SceneGraphSchema>;
export type SceneObject = SceneGraph['objects'][number];

export interface Frame {
  image?: LlmImage;
  /** Selects a mock fixture; ignored by live providers. */
  fixture?: string;
}

export interface VisionProvider {
  readonly label: 'ANTHROPIC' | 'MOCK AI';
  describe(frame: Frame): Promise<SceneGraph>;
}

export const SCENE_FIXTURES: Record<string, SceneGraph> = {
  lobby: {
    summary: 'A lobby with a cleaning trolley close behind you, a wet floor sign ahead, and an EXIT sign to your left.',
    objects: [
      { label: 'cleaning trolley', kind: 'obstacle', bearingDeg: 180, clock: 6, distance: 'near', distanceM: 3, confidence: 0.74 },
      { label: 'door', kind: 'door', bearingDeg: 300, clock: 10, distance: 'mid', distanceM: 6, confidence: 0.68 },
      { label: 'bench', kind: 'furniture', bearingDeg: 60, clock: 2, distance: 'near', distanceM: 2, confidence: 0.81 },
      { label: 'reception desk', kind: 'furniture', bearingDeg: 0, clock: 12, distance: 'far', distanceM: 9, confidence: 0.77 },
    ],
    signs: [
      { text: 'EXIT', bearingDeg: 270, clock: 9, distance: 'far', confidence: 0.83 },
      { text: 'Wet floor', bearingDeg: 15, clock: 12, distance: 'mid', confidence: 0.71 },
    ],
    peopleCount: 2,
    hazards: [{ label: 'wet floor sign', severity: 'medium', bearingDeg: 15, clock: 12, distance: 'mid', confidence: 0.71 }],
    lighting: 'normal',
  },
  corridor: {
    summary: 'A narrow corridor with a fire door ahead and a step down on the right.',
    objects: [
      { label: 'fire door', kind: 'door', bearingDeg: 0, clock: 12, distance: 'mid', distanceM: 5, confidence: 0.79 },
      { label: 'step down', kind: 'stairs', bearingDeg: 90, clock: 3, distance: 'near', distanceM: 1.5, confidence: 0.6 },
    ],
    signs: [{ text: 'Fire door keep shut', bearingDeg: 0, clock: 12, distance: 'mid', confidence: 0.66 }],
    peopleCount: 0,
    hazards: [{ label: 'step down', severity: 'medium', bearingDeg: 90, clock: 3, distance: 'near', confidence: 0.6 }],
    lighting: 'dim',
  },
};

export class MockVisionProvider implements VisionProvider {
  readonly label = 'MOCK AI' as const;
  async describe(frame: Frame): Promise<SceneGraph> {
    return structuredClone(SCENE_FIXTURES[frame.fixture ?? 'lobby'] ?? (SCENE_FIXTURES.lobby as SceneGraph));
  }
}

export class LlmVisionProvider implements VisionProvider {
  constructor(private readonly llm: LlmClient) {}
  get label(): 'ANTHROPIC' | 'MOCK AI' {
    return this.llm.label;
  }

  async describe(frame: Frame): Promise<SceneGraph> {
    if (!frame.image) throw new Error('a camera frame or image is required');
    const { value } = await structured(this.llm, SceneGraphSchema, {
      system: `You describe a camera image for a blind or low-vision user as a JSON scene graph. ${DATA_NOT_INSTRUCTIONS}`,
      prompt:
        'Return ONLY a JSON object with keys: summary (<=300 chars), objects[{label,kind,bearingDeg,clock,distance,distanceM?,confidence}], ' +
        'signs[{text,bearingDeg,clock,distance,confidence}], peopleCount, hazards[{label,severity,bearingDeg,clock,distance,confidence}], lighting. ' +
        'bearingDeg is clockwise from straight ahead (0-360); clock is 1-12; distance is near|mid|far; confidence is 0-1. ' +
        'kind is one of object|door|exit|obstacle|furniture|stairs|vehicle; severity is low|medium|high; lighting is dark|dim|normal|bright.',
      images: [frame.image],
    });
    return value;
  }
}
