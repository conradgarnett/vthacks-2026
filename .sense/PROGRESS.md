# Progress

Append one entry per milestone: date, what was done, verify result, tag.

## M0 — Bootstrap (in progress)
- Portable Node installed to `.tools/node`, workspaces installed, configs written.
- M0 done: `npm run verify` green. Tag `m0-done`.

## M1 — Protocol (done)
- Zod schemas: Percept (10-word `short` cap; safety percepts must name tier + source), Provenance, SecurityEvent, SensoryProfile + SignedProfile, five personas + custom builder + situational presets, Sense Card (+ signed), per-capability payloads, agent request/response messages, `SenseModule`, injected clock + seeded PRNG.
- JSON Schema export (`npm run schemas` -> docs/schemas), docs/SENSE_CARD_SPEC.md with a test that its example validates.
- verify green (23 tests). Tag `m1-done`.

## M2 — Identity (done)
- `packages/identity`: ECDSA P-256 WebCrypto helpers, `LocalCA` (server CA + identity CA, real X.509 via @peculiar/x509, FQDN-bound server cert, per-version identity cert with `ans://v{ver}.{fqdn}` URI SAN and digest extension), signed CRL, RFC 6962-style Merkle log with inclusion proofs + signed tree heads + pinned-head append-only check, `SimulatedRegistry` (register / registerUnlogged / revoke / search / outage switches), 7-step verification pipeline (`runVerification`), `SimulatedAnsClient`, `LiveAnsClient` typed stub (throws NotConfigured), `PublisherIdentity` helper, key store under gitignored `.sense/keys`.
- docs/ANS_NOTES.md written from 3 real fetches (governance wording differs from the brief; recorded).
- 37 identity tests: happy path, every failure mode naming its step, UNVERIFIED degradation, Merkle tamper detection for all sizes 1..33. Coverage 92% statements / 95% lines.
- verify green. Tag `m2-done`.

## M3 — World-sim (done)
- `apps/world-sim`: SimAgent base (validates requests, minimum-scope enforcement, signed responses/pushes, subscribe/unsubscribe, heartbeat), `WorldSim` (hostname-map transport `SimTransport`, ANS-modeled registry, deterministic seeded RNG, timeline + named scripts fire/smoke/spoof/flood, manual-clock friendly `tick()`).
- Honest publishers: riverside-hall.sim (indoor-map, alarm-feed, air-quality, accessibility-features), bella-cucina.sim (menu-allergens), metro-transit.sim (arrivals), city-air.sim (air-quality), hall-lifts.sim (device-control).
- Attackers (activated on demand): impersonator, revoked, codeSwap, unlogged, spoofer (rogue CA + unsolicited push), injector (VERIFIED kiosk with hostile notes), flooder (VERIFIED noisy signs).
- HTTP view with virtual hosting (Host header or /agents/:fqdn path) + /.well-known/sense-card.json + SSE events + /sim/event control.
- 21 world-sim tests. verify green (81 tests total). Tag `m3-done`.
