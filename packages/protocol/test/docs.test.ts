import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { SenseCardSchema } from '../src';

describe('docs/SENSE_CARD_SPEC.md', () => {
  it('its JSON example is a valid Sense Card', () => {
    const md = readFileSync(join(import.meta.dirname, '../../../docs/SENSE_CARD_SPEC.md'), 'utf8');
    const match = /```json\n([\s\S]*?)\n```/.exec(md);
    expect(match).not.toBeNull();
    const card = SenseCardSchema.parse(JSON.parse(match?.[1] ?? '{}'));
    expect(card.capabilities.map((c) => c.id)).toContain('alarm-feed');
  });
});
