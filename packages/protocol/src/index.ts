import { z } from 'zod';
import { PerceptSchema, SecurityEventSchema } from './percept';
import { SensoryProfileSchema, SignedProfileSchema } from './profile';
import { SenseCardSchema, SignedSenseCardSchema } from './sensecard';
import { AgentRequestSchema, AgentResponseSchema } from './messages';
import { PAYLOAD_SCHEMAS } from './payloads';

export * from './util';
export * from './clock';
export * from './percept';
export * from './profile';
export * from './sensecard';
export * from './payloads';
export * from './messages';
export * from './module';
export * from './personas';

/** All exported JSON Schemas (draft 2020-12), keyed by file name stem. See `npm run schemas`. */
export function jsonSchemas(): Record<string, unknown> {
  const out: Record<string, unknown> = {
    percept: z.toJSONSchema(PerceptSchema),
    'security-event': z.toJSONSchema(SecurityEventSchema),
    'sensory-profile': z.toJSONSchema(SensoryProfileSchema),
    'signed-profile': z.toJSONSchema(SignedProfileSchema),
    'sense-card': z.toJSONSchema(SenseCardSchema),
    'signed-sense-card': z.toJSONSchema(SignedSenseCardSchema),
    'agent-request': z.toJSONSchema(AgentRequestSchema),
    'agent-response': z.toJSONSchema(AgentResponseSchema),
  };
  for (const [capability, schema] of Object.entries(PAYLOAD_SCHEMAS)) {
    out[`payload-${capability}`] = z.toJSONSchema(schema);
  }
  return out;
}
