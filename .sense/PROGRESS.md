# Progress

Append one entry per milestone: date, what was done, verify result, tag.

## M0 — Bootstrap (in progress)
- Portable Node installed to `.tools/node`, workspaces installed, configs written.
- M0 done: `npm run verify` green. Tag `m0-done`.

## M1 — Protocol (done)
- Zod schemas: Percept (10-word `short` cap; safety percepts must name tier + source), Provenance, SecurityEvent, SensoryProfile + SignedProfile, five personas + custom builder + situational presets, Sense Card (+ signed), per-capability payloads, agent request/response messages, `SenseModule`, injected clock + seeded PRNG.
- JSON Schema export (`npm run schemas` -> docs/schemas), docs/SENSE_CARD_SPEC.md with a test that its example validates.
- verify green (23 tests). Tag `m1-done`.
