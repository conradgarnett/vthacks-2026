import { z } from 'zod';
import type { CapabilityId } from './sensecard';

/**
 * Per-capability payloads. Remote agent content is DATA: every payload is parsed with a strict schema
 * (unknown keys rejected, strings and arrays length-limited) before anything else touches it.
 */
const text = (max: number) => z.string().max(max);
const id = () => z.string().min(1).max(64);
const point = z.strictObject({
  x: z.number().min(-100_000).max(100_000),
  y: z.number().min(-100_000).max(100_000),
});
export type Point = z.infer<typeof point>;

export const IndoorMapPayloadSchema = z.strictObject({
  floor: text(16),
  nodes: z
    .array(
      z.strictObject({
        id: id(),
        kind: z.enum(['entrance', 'exit', 'stairs', 'lift', 'room', 'corridor', 'assembly-point']),
        label: text(80),
        x: z.number(),
        y: z.number(),
      }),
    )
    .max(200),
  edges: z.array(z.strictObject({ from: id(), to: id(), meters: z.number().min(0).max(10_000) })).max(400),
  notes: text(300).optional(),
});
export type IndoorMapPayload = z.infer<typeof IndoorMapPayloadSchema>;

export const AlarmFeedPayloadSchema = z.strictObject({
  alarms: z
    .array(
      z.strictObject({
        id: id(),
        state: z.enum(['active', 'cleared', 'test']),
        type: z.enum(['fire', 'smoke', 'evacuation', 'other']),
        zone: text(80),
        location: point,
        raisedAt: z.iso.datetime(),
        message: text(300).optional(),
      }),
    )
    .max(32),
});
export type AlarmFeedPayload = z.infer<typeof AlarmFeedPayloadSchema>;

export const READING_KINDS = ['smoke', 'co', 'voc', 'pm25', 'aqi'] as const;
export const AirQualityPayloadSchema = z.strictObject({
  readings: z
    .array(
      z.strictObject({
        sensorId: id(),
        kind: z.enum(READING_KINDS),
        value: z.number().min(0).max(1_000_000),
        unit: text(16),
        measuredAt: z.iso.datetime(),
        location: point.optional(),
        label: text(80).optional(),
      }),
    )
    .max(64),
});
export type AirQualityPayload = z.infer<typeof AirQualityPayloadSchema>;

export const MenuAllergensPayloadSchema = z.strictObject({
  restaurant: text(80),
  items: z
    .array(
      z.strictObject({
        id: id(),
        name: text(80),
        description: text(300).optional(),
        ingredients: z.array(text(60)).max(60),
        allergens: z.strictObject({
          contains: z.array(text(40)).max(20),
          mayContain: z.array(text(40)).max(20),
        }),
        preparation: text(200),
        spice: z.number().int().min(0).max(5),
        texture: text(80).optional(),
        temperature: z.enum(['cold', 'cool', 'warm', 'hot']).optional(),
        culture: text(200).optional(),
        notes: text(300).optional(),
      }),
    )
    .max(80),
});
export type MenuAllergensPayload = z.infer<typeof MenuAllergensPayloadSchema>;

export const ArrivalsPayloadSchema = z.strictObject({
  stop: text(80),
  arrivals: z
    .array(
      z.strictObject({
        route: text(16),
        destination: text(80),
        etaMinutes: z.number().min(0).max(1440),
        platform: text(8).optional(),
        accessible: z.boolean(),
      }),
    )
    .max(32),
});
export type ArrivalsPayload = z.infer<typeof ArrivalsPayloadSchema>;

export const AccessibilityFeaturesPayloadSchema = z.strictObject({
  features: z
    .array(
      z.strictObject({
        kind: z.enum([
          'step-free-route',
          'hearing-loop',
          'tactile-paving',
          'braille-signage',
          'accessible-toilet',
          'lift',
          'quiet-room',
          'visual-alarm',
        ]),
        location: text(80).optional(),
        notes: text(300).optional(),
      }),
    )
    .max(40),
});
export type AccessibilityFeaturesPayload = z.infer<typeof AccessibilityFeaturesPayloadSchema>;

export const DeviceControlPayloadSchema = z.strictObject({
  devices: z
    .array(
      z.strictObject({
        id: id(),
        kind: z.enum(['elevator']),
        label: text(80),
        state: text(80),
        actions: z.array(z.enum(['call'])).max(4),
      }),
    )
    .max(16),
  result: z.strictObject({ ok: z.boolean(), message: text(200) }).optional(),
});
export type DeviceControlPayload = z.infer<typeof DeviceControlPayloadSchema>;

export const PAYLOAD_SCHEMAS = {
  'indoor-map': IndoorMapPayloadSchema,
  'alarm-feed': AlarmFeedPayloadSchema,
  'air-quality': AirQualityPayloadSchema,
  'menu-allergens': MenuAllergensPayloadSchema,
  arrivals: ArrivalsPayloadSchema,
  'accessibility-features': AccessibilityFeaturesPayloadSchema,
  'device-control': DeviceControlPayloadSchema,
} as const satisfies Record<CapabilityId, z.ZodType>;

export type PayloadFor<C extends CapabilityId> = z.infer<(typeof PAYLOAD_SCHEMAS)[C]>;
