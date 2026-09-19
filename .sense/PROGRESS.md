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

## M8 — Hearing / Echo (done)
- `packages/senses/hearing`: dependency-free DSP (radix-2 FFT, Hann, tonality/peakiness, envelope, centroid), deterministic synth (siren wail, knocks, beeps, stereo panning), `LocalHeuristicClassifier` (siren-like sweep, beep alarm, knock transients, "unknown" rather than guessing), stereo level-difference direction (left/right only, mono => unknown), `WindowedClassifier` for streams, `ScriptedSoundscape` (labelled scripted), `EchoSense` percept builder: labels always INFERRED with confidence, urgency capped at 3, personalised priority (Deaf gets knock/doorbell +1), debounce, verified building alarm dominates (inferred siren demoted to a corroborating note). 26 tests incl. a 200-run property test.
- Server `/api/sound`, `/api/soundscape` (only sound EVENTS reach the server; audio stays on the device); web HearingPanel (simulated soundscape, synthetic siren analysed on-device, local microphone). verify green.

## M9 — ScentGuard (done)
- `packages/senses/scent`: illustrative rule table (S1-S4 smoke, C2-C4 CO, V1-V2 VOC, A1-A2 AQI, P1-P2 PM2.5) + combination rules X1/X2 with a fire alarm from a VERIFIED source; pure `assess()`; `ScentGuard` emits percepts on change with rule ids, readings, ages, thresholds and cautions. docs/SCENTGUARD_RULES.md is kept in sync with the code by a test.
- Honesty: missing/stale/unverified data keeps the last known level as UNVERIFIED and says "unknown now"; level 0 only means "no elevated reading reported by fresh verified sensors"; regional data capped at level 2; only a VERIFIED level 4 is urgency 4. 22 tests incl. property tests (monotonic, never VERIFIED when stale, never assurance wording).
- Server glue: `scentInputs` (identity tier + per-reading timestamps), 'scent' server event, watchdog re-evaluation, `broker.pollUnsubscribed()` for regional feeds that cannot push (arrive now falls back to a query). Bug found by the UI test: scent status was not pushed to the browser; fixed with a `scent` event. Web ScentPanel (text + meter + tier badge). verify green.

## M10 — TasteLens (done)
- `packages/senses/taste`: allergen vocabulary (canonical names, synonyms, custom allergens by word), label parser (contains / may-contain / traces / ingredient list; each sentence sanitized so an injected line is dropped without losing the real list), claim collection from verified menu record / label text / photo inference, precedence verified agent > label > photo, alerts for declared allergens from ANY tier stating the tier, "may contain" treated as present, honest "not listed" / "not detected, unverified" wording (never "safe"), multisensory facets (texture, spice, salt, sweet, acid, umami, richness, temperature, aroma, ingredients, allergens, preparation, culture) with separate provenance for verified vs inferred parts, graceful degradation when the photo model fails, the model never receives the user's allergens. 18 tests incl. a 400-run property test of the allergen invariants.
- Server `/api/menu`, `/api/taste`; web TastePanel; `arrive` now also fetches menu-allergens (static, low-sensitivity). Integration test proves nothing about the allergen or profile is sent to any publisher. verify green.

## M11 — Touchless (done)
- `packages/senses/touchless`: one `InputDevice` interface producing intents (select, next, back, activate, acknowledge); drivers: one-switch auto-scan (adjustable speed, press/long-press), dwell-to-select over a pointer stream (tremor smoothing = EMA + dead band, adjustable dwell, hysteresis margin, refractory period, quick passes never activate), linear calibration fit, keyboard intents, scripted driver for demos, and a webcam head/eye/hand seam that is honestly UNAVAILABLE (no MediaPipe model files vendored; never fakes tracking). `IntentController` maps intents to app targets with a false-activation guard, an always-available pause, and `via = intent:<device>` on every action. `ManualScheduler` for deterministic runs. 23 tests.
- Web: TouchlessPanel (device choice, scan speed, dwell time, pause + Escape, calibration with example drift, verified lift call), `data-touchless` targets, visible focus ring, `store.runVia` so every server call started by a device is audited as `intent:<device>`. Server `/api/lift` (device-control over the verified channel, minimum scope `lift:call`, refuses during alarm).
- Scene 6 proven in the UI test: Motor persona acknowledges the alert and switches persona with only the switch driver; zero pointer/mouse events recorded; audit trail shows `intent:switch-scan`. Bugs found by tests/lint and fixed: decorative calibration dots were mouse-only buttons; panel restructured for React hooks lint rules. verify green (418 tests).

## M12 — Demo orchestration and verification (done)
- `apps/server/src/scenes.ts`: the six golden scenes + an extra attacker scene (7), each with explicit assertions, driven only through the server's HTTP API. One module powers `demo:verify`, `demo:cli`, live auto-play, and a vitest that runs every scene.
- `npm run demo:verify`: headless, OFFLINE (global fetch throws; asserts zero network calls), manual clock, exit code 0 only if all 69 checks pass (~2 s). Measured pushed-alarm latency ~200 ms in-process (limit 300).
- `npm run demo:cli`: colored provenance tags + persona routing + security events. `npm run demo [-- --auto --open --dev]`: launcher (builds web if needed, starts the demo-mode server, prints/opens the URL, optional auto-play); verified against the real process (7 scenes, 63 checks, 0 failed). `npm run seed` prints the simulated world.
- Demo mode: OffsetClock ("SIMULATED TIME SKIP"), /api/demo/* routes, DemoPanel in the UI, world-sim HTTP window on its own port (Host-header virtual hosting). Bugs found and fixed: `activate-attackers` world event did not await registration (race); DemoPanel crashed outside demo mode (now validates response shape; test harness mirrors the real client's error behaviour).
- Playwright is not used; UI flows are covered by Testing Library tests against the real server.

## M13 — Docs and pitch (done)
- README (thesis, quickstart, mode table of what is real vs simulated, limitations notice, repo map), docs/ARCHITECTURE.md (Mermaid: components, verification sequence, remote-content pipeline), THREAT_MODEL.md (assets, adversaries, the eight attacks with tests, DNS/CA dependence, residual risk), DEMO_SCRIPT.md (3-minute and 60-second), PITCH.md (only supportable claims), ROADMAP.md, PUBLISH_AN_AGENT.md (measured onboarding timing, and what was NOT measured), SENSE_CARD_SPEC.md, SCENTGUARD_RULES.md, ANS_NOTES.md.
- `scripts/docs.test.ts` keeps docs honest: every `npm run` mentioned exists, every relative link resolves, README carries the limitations notice and mode labels, threat model covers all eight attacks, `.env.example` matches the env vars the code reads, JSON Schemas on disk match the code.
- Scale seams proven: `packages/senses/crowd` (64 lines, imports only @sense/protocol) and `create-sense-agent` (generates a working agent that passes 7/7 in ~2 s of machine time).
