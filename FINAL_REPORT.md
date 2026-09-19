# SENSE: final report

**Status:** all P0 and P1 milestones (M0 to M14) are built, tested and committed locally with tags
`m0-done` to `m14-done`. This is a **hackathon-grade prototype**. Everything it demonstrates is
**simulated**, and it is **not a medical device and not a certified safety system**.

## Definition of Done

| Requirement                                                            | Result | Evidence                                                                                          |
| ---------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------- |
| `npm ci && npm run verify` passes from a clean clone                   | Yes    | Cloned to a scratch directory: `npm ci` 13 s, `verify` 255 s, 439/439 tests, build OK, `git status` clean afterwards |
| `npm run demo:verify` passes offline with all six scenes + extras      | Yes    | 69/69 checks in ~2 s; global `fetch` is made to throw and the run asserts zero network calls      |
| Golden path demoable in the web app with all five personas             | Yes    | Real server behind the real React app in tests; live launcher run with auto-play (7 scenes, 63 checks, 0 failed) |
| All eight attacker scenarios have tests and pass                       | Yes    | `world-sim`, `core/broker.test.ts`, `core/invariants.test.ts`, demo scene 7                        |
| Safety invariants suite passes                                         | Yes    | `packages/core/test/invariants.test.ts` (12 tests, property-based)                                |
| Every simulated component is labelled simulated in UI and docs         | Yes    | Persistent SIMULATED WORLD / ANS-modeled / MOCK AI / Offline badges; per-card SIMULATED badge; docs say so |
| Docs in section 9 exist and are consistent with the code               | Yes    | `scripts/docs.test.ts` checks links, commands, env vars, schemas, required statements             |
| No secrets committed; `.env.example` complete                          | Yes    | Secrets scan clean; keys live in gitignored `.sense/keys`; a test compares `.env.example` to the env vars the code reads |
| `FINAL_REPORT.md` with an honest list of gaps                          | Yes    | This file, section "Known gaps"                                                                   |
| Work committed locally with milestone tags                             | Yes    | `git tag` shows `m0-done` … `m14-done`. Nothing was pushed.                                       |

## What was built, by milestone

| Milestone | Delivered                                                                                                                                                       |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M0        | npm workspaces, strict TypeScript, ESLint + Prettier, Vitest, `verify`, `.env.example`, `.gitignore`, `.sense/` state files, connectivity check                 |
| M1        | Zod schemas (Percept with a 10-word speakable `short`, Provenance, Profile, Sense Card, agent messages, payloads), five personas, situational presets, JSON Schema export, Sense Card spec |
| M2        | ANS-**modeled** identity: two-root local CA, real X.509 (ECDSA P-256), signed revocation list, RFC 6962-style Merkle log with inclusion proofs, 7-step verification, `LiveAnsClient` stub (`NotConfigured`), key store |
| M3        | World-sim: 5 honest publishers, 7 attackers, deterministic seeded timeline and scripts, hostname-map transport, HTTP view with Host-header virtual hosting          |
| M4        | Broker: discovery by area/capability, verification, ephemeral sessions, subscriptions, sanitizer, trust engine, conflict policy, rate limiter, disclosure gate, frozen policy, signed portable profile |
| M5        | Routing matrix (all 500 persona/sense/urgency/safety/spatial combinations checked), earcons, haptic patterns, Web Speech / Web Audio / Vibration renderers with honest fallbacks |
| M6        | Local server (HTTP + WebSocket), accessible React app: persona switcher, feed with provenance badges, Trust Inspector, Disclosure Log, security events, attacker scene |
| M7        | Vision: scene memory (heading-aware), routing over the verified map, question answering with per-statement provenance, mock + Anthropic providers with validate/retry/degrade |
| M8        | Hearing/Echo: hand-written DSP (FFT, tonality, transients), siren/knock/beep detection, stereo direction, scripted soundscape, verified-alarm dominance             |
| M9        | ScentGuard: transparent rule table, escalation levels 1 to 4, stale/unknown handling, regional context                                                            |
| M10       | TasteLens: allergen policy (precedence, any-tier alerts, may-contain = present, never "safe"), multisensory description, label parser                             |
| M11       | Touchless: one `InputDevice` interface; switch scanning, dwell with tremor smoothing and guards, keyboard, scripted, calibration, pause; verified lift control       |
| M12       | Six golden scenes + attacker scene as one assertion-carrying module; `demo`, `demo:verify`, `demo:cli`, `seed`, live auto-play, simulated time skip               |
| M13       | Full documentation set; `crowd` plugin (64 lines); `create-sense-agent` template; docs consistency test                                                          |
| M14       | Dependency audit, dead-code removal, whole-document axe across all personas, clean-clone verification, this report                                                |

## Simulated versus real

**Real (works and is tested):** X.509 certificates, ECDSA signatures, Merkle inclusion proofs, revocation
lists; schema validation, sanitization, rate limiting, the disclosure gate; the routing matrix and
renderers; the DSP for sounds (FFT, tonality, transients, stereo level difference); input-device logic;
keyboard, ARIA and contrast handling; the local server and WebSocket push.

**Simulated or stubbed:** the registry, CA and DNS (a local `.sim` world: no ACME, no public CA, no
DNSSEC); publishers, sensors, attackers, the user's position and the soundscape; the "AI" (mock fixtures
offline); `LiveAnsClient` (throws `NotConfigured`); the Anthropic client is implemented but was **never
exercised against the live API** (no key was available). The webcam head/eye/hand tracker is
**unavailable** and says why.

## Test results

- **439 tests, 439 passed, 0 failed** across 24 test files and 144 suites (`npm test`).
- **`npm run demo:verify`: 69 checks, all passed** (7 scenes + 6 global checks), offline, ~2 s.
- **Property-based tests (fast-check):** allergen policy (400 runs over every source combination), alarm never suppressed under hostile interleavings, stale never VERIFIED, profile never outbound, sanitizer, rate limiter, tremor filter, ScentGuard monotonicity, Echo urgency cap.
- **Accessibility:** axe-core on every main screen and on the **whole document with page-level rules for all five personas** (no violations); keyboard-only walkthrough that operates the golden path without a mouse; colour contrast computed from the CSS tokens for light, dark and high-contrast themes (text ≥ 4.5:1, UI boundaries ≥ 3:1).
- **Touchless (scene 6):** in the real UI, only a scanning driver acknowledges an alert and switches persona; pointer/mouse event listeners record nothing; the server's audit trail shows every action as `intent:switch-scan`.

| Area                   | Tests | Coverage (statements / lines) |
| ---------------------- | ----- | ----------------------------- |
| `packages/protocol`    | 23    | 97.7% / 97.7%                 |
| `packages/identity`    | 37    | **92.8% / 97.4%** (target ≥ 90%) |
| `packages/core`        | 78    | **90.3% / 92.6%**; trust engine (`trust.ts`) **97.2% / 100%** (target ≥ 90%) |
| `packages/providers`   | 14    | 98.9% / 100%                  |
| `packages/render`      | 28    | 98.7% / 99.2%                 |
| `senses/vision`        | 32    | 98.8% / 100%                  |
| `senses/hearing`       | 26    | 96.1% / 100%                  |
| `senses/scent`         | 22    | 98.7% / 99.2%                 |
| `senses/taste`         | 18    | 100% / 100%                   |
| `senses/touchless`     | 23    | 98.3% / 99.6%                 |
| `senses/crowd`         | 4     | 100% / 100%                   |
| `apps/world-sim`       | 21    | 88.6% / 90.8%                 |
| `apps/server`          | 30    | 90.8% / 91.5%                 |
| `apps/web`             | 71    | **70.8% / 71.2%** (lowest; see gaps) |
| `scripts`              | 12    | n/a                           |
| **Total**              | 439   | 91.5% / 93.0%                 |

The safety-invariant cases listed in the brief are all covered (100% of listed cases).

**Performance (alert-to-render, pushed VERIFIED alarm, localhost, in-process):** the first alert in a
cold process takes about 200 ms (JIT plus first signature verification); warm runs are about 1 ms median
(`npm run latency`: broker 1.0 ms, WebSocket client 1.1 ms median, p95 1.4 ms). The target of 300 ms holds.
**Browser paint time is not included**, and no real device was measured.

**Dependencies:** `npm audit`: 0 vulnerabilities. ESLint and TypeScript are deliberately pinned to
older majors for tool compatibility (see D3, D4 in `.sense/DECISIONS.md`).

## Known gaps (please read)

**Not real**
1. No real ANS integration, ACME domain-control validation, DNSSEC or public CA. The identity layer is a faithful-in-spirit model, not ANS.
2. The Anthropic client is implemented and unit-tested with a stubbed `fetch`, but **has never been run against the live API**. Vision and taste "AI" are offline mock fixtures.
3. No real sensors, buildings, restaurants or positioning. The user's position and heading come from the simulation.
4. The optional **live air-quality API** (M9, "when the network is available") was **not built**; only simulated and mock data are used. (`SENSE_LIVE_AIR` was removed from `.env.example` because nothing reads it.)
5. **Webcam head/eye/hand tracking is unavailable**: no MediaPipe model files were vendored. Switch scanning, keyboard intents, dwell over any pointer stream, and a scripted driver work. Dwell in the app uses the mouse as a stand-in for a tracker.

**Not tested or evaluated**
6. **No evaluation with disabled users, no accessibility audit, no screen-reader testing** (NVDA, JAWS, VoiceOver, TalkBack), no real haptic hardware, no real speech synthesis output, no real 200%-zoom reflow check. WCAG 2.2 AA is a **target**; compliance is not claimed. Automated checks run in jsdom, which has no layout: contrast is computed from tokens, not from rendered pixels.
7. **No real-browser end-to-end tests** (Playwright was not installed; see D29). The microphone and camera capture code (`startMic`, `startCamera`) has not been exercised in a real browser. Web app coverage is 70.8%.
8. The sound classifier was tested only on **synthetic signals**. Its accuracy on real recordings is unknown. It is hand-written rules, not a trained model, and labels can be wrong.
9. Developed and tested on **Windows 11 with Node 24** only.

**Simplifications**
10. TasteLens's allergen dictionary is small and English-only, and folds "gluten" into "wheat". The vision question parser is rule-based: free-form questions get an honest "I did not understand".
11. ScentGuard thresholds are **illustrative**, not from any standard.
12. Protocol simplifications: no RFC 6962 consistency proofs (a pinned tree head is used instead); the session's ephemeral key is not yet used to sign requests; the broker's transport is an in-process hostname map (the HTTP view of the world is for inspection; an HTTP broker transport was not built).
13. The portable signed profile (export/import) exists in the API and core library and is tested, but the **UI has no export/import buttons**.
14. `arrive` connects to every agent that covers the area; there is no location-aware filtering beyond the area label.

**Claims not established**
15. "A business publishes an accessibility agent in 10 minutes" is **supported only for the scaffolding** (about 2 s of machine time, measured). Human effort with real publishers was not measured.
16. The premise that publishers will adopt Sense Cards is a hypothesis.

## Blockers

`.sense/BLOCKERS.md` is empty: the five-attempt rule never triggered. Fallbacks used from section 11:
Playwright not installed (API-driven e2e + Testing Library instead) and webcam model assets unavailable
(switch scanning + keyboard + scripted driver, gap documented). Notable problems that tests, lint or
`demo:verify` caught and that were fixed are listed in `.sense/PROGRESS.md` (for example a broker
race that started attackers before `arrive`, an allergen-matching bug in the disclosure gate, scent status
not reaching the browser, and mouse-only decorative controls). One lint failure was committed before the
next commit fixed it because an early shell check read the wrong exit status; from M7 on, `verify` runs
with `pipefail` and its real exit code is checked (D28).

## Decisions worth reviewing (`.sense/DECISIONS.md`)

- **D2** A portable Node was unpacked into a gitignored `.tools/` because the machine had none and global installs were forbidden.
- **D9** `Percept.short` is capped at 10 words (assumed 2.8 words/s at 1x, alerts at ≥ 1.8x) and safety percepts must name their tier and source inside it.
- **D10 / D20 / D21 / D22** Strict schemas reject unknown keys; the sanitizer drops whole fields; unsolicited pushes are never routed even from verified agents; unverified sources cannot produce urgency 4.
- **D13** An unavailable check yields `UNVERIFIED`, a failed check `REJECTED`; the first failure names its step.
- **D25** The "server" is the user's local process, where the profile lives; only the outbound gate protects what leaves.
- **Blind persona** translates vision only; other senses reach a blind user through the safety rule (safety percepts always get full escalation).
- `arrive` also fetches `menu-allergens` (static, low-sensitivity) so menus are ready when the user reaches a restaurant.
- **D29 / D31** Playwright and the webcam tracker were deliberately not done.

## How to run everything

```bash
npm install                    # Node >= 20.19
npm run verify                 # typecheck + lint + format check + tests + build
npm run demo:verify            # headless, offline, six scenes + extras; exit 0 only if all hold
npm run demo:cli               # same scenes as colored text
npm run demo                   # live app; add  -- --auto  to play scenes,  -- --open  to open the browser
npm run latency                # alert-to-render latency measurement
npm run test:coverage          # coverage summary
npm run world-sim              # the simulated world alone, one hostname per agent
npm run seed                   # create local dev keys and list the simulated world
npm run schemas                # export JSON Schemas to docs/schemas
npm run create-sense-agent -- my-cafe.sim --name "My Cafe" --capability menu-allergens
```
