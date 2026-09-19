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

## M4 — Core broker (done)
- `packages/core`: sanitizer (unicode cleanup + instruction/assurance detection, drops whole field), safety guard (assurance-wording ban, redaction of remote labels, REJECTED never routed), trust engine (freshness from payload timestamps, tierFor, conflict reconcile), rate limiter (dedupe + token bucket, verified urgency>=3 always admitted), disclosure gate (strict schema + profile leak scan + log), frozen policy, signed portable profile, percept mapping, and `SenseBroker` (discover/arrive, ephemeral-session hello + 7-step verify, query/subscribe/unsubscribe, signed+schema-validated+sanitized ingest, unsolicited-push handling, freshness watchdog).
- Tests: units (39), broker integration (23: every 5.4 scenario), invariants suite (12, property-based), profile-io (4). Core coverage 92% stmts / 94% lines.
- Bugs found by tests and fixed: disclosure gate matched "allergens" in capability id `menu-allergens`; conflict percept lacked source label; push handlers now fail loud into the security log instead of unhandled rejections; fitShort keeps sentence punctuation.
- verify green (159 tests). Tag `m4-done`.

## M5 — Profiles and rendering (done)
- `packages/render`: `route(percept, profile) -> RenderPlan` (modality selection with per-sense overrides, safety redundancy >= 2 modalities at urgency >= 3, untranslated-sense quieting, caption for every audio output, ARIA politeness, interruption), speech text with spoken hedging of inferred/unverified content, earcon schedules per urgency, stereo pan + HRTF panner positions, haptic patterns with direction prefix + text equivalents, browser renderers (Web Speech, Web Audio, Vibration) behind injectable envs with honest fallbacks, `Presenter`.
- Personas: Blind persona now translates vision only. Situational presets (hands-full, noisy-room) covered by routing tests.
- 28 render tests including an all-combinations invariant sweep (5 personas x 5 senses x 5 urgencies x safety x spatial = 500 cases). verify green.

## M6 — Golden path vertical slice (done)
- `apps/server`: Fastify local API (state snapshot, arrive, persona/custom/situation/allergens, signed profile export/import, ack with `x-sense-via` audit, world events/scripts), WebSocket push (`/ws`), static serving of the built web app, keys in gitignored `.sense/keys`. 14 tests including alert->browser websocket latency.
- `apps/web`: React app with persona switcher (radio group), situational presets, allergens, custom profile builder, simulated-world controls, live percept feed with provenance badges (symbol + word + color), assertive/polite live regions, haptic visual fallback, Trust Inspector (7 steps + evidence), Disclosure Log, Security events, persistent SIMULATED WORLD / ANS-modeled / MOCK AI / Offline badges, limitations notice. Real server in-process behind the real UI in tests; axe clean; keyboard-only walkthrough test; colour contrast computed from CSS tokens for light/dark/high-contrast.
- Real-process smoke test done (boot server, curl arrive + alarm, no profile leak). verify green (263 tests). Tag `m6-done`.

## M7 — Vision (done)
- `packages/providers`: LlmClient abstraction, `structured()` (validate, retry once with feedback, then ProviderDegraded), AnthropicClient over fetch (stub-tested only; NOT exercised against the live API), mock vision/taste fixtures, SceneGraph schema with no field for identifying people, `createProviders(env)` (mock unless key present).
- `packages/senses/vision`: SceneMemory (heading-aware bearings, decay, TTL), Dijkstra exit routing over the verified map, rule-based question parser, answer engine producing statements each with own provenance (VERIFIED map vs INFERRED camera, sign/map reconciliation and disagreement flagging), honest fallbacks (no map, nothing seen, not understood), sanitized model output, `SenseModule` seam. 32 tests.
- Server `/api/see` `/api/ask`; web VisionPanel (sample view offline, upload, device camera). `SenseContext` now owns providers + vision; mode.ai comes from providers.
- Broker `arrive` now connects safety-feed agents first (found via a test race). verify green.
