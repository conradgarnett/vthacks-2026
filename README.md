# SENSE

> Alt text for physical reality, plus an agent that can read it safely.

**A hackathon-grade prototype. Everything in the default demo is simulated and labelled as such.**

SENSE gives people alternative ways to perceive the world when a biological sense gives them too
little information. It translates the world into speech, text, spatial audio, haptics and visuals,
in a personal profile that stays on your device.

## The idea

**SENSE should not be the thing that perceives the world. It should be the thing that finds and trusts
the parts of the world that already perceive themselves.**

Buildings have fire panels and floor plans. Restaurants know their ingredients. Cities have
air-quality sensors. Today that knowledge is unreachable, or untrustworthy, for a person's assistant.
In SENSE:

- each user has a personal **SENSE agent** holding their sensory profile, locally;
- places and services publish their own agents under domains they control, with a **Sense Card**
  (what they can tell you, from what basis, how fresh);
- the SENSE agent **discovers** them, **verifies** who they are through domain-anchored identity
  (modeled on the Agent Name Service), **talks** to them, and translates the result for you;
- camera and microphone AI are the **fallback** when no verified source exists;
- every statement carries visible **provenance**: `VERIFIED`, `INFERRED`, `UNVERIFIED` or `REJECTED`.

| Sense              | Who it helps            | Example output                                                        |
| ------------------ | ----------------------- | --------------------------------------------------------------------- |
| Vision             | Blind / low vision      | "Exit: Main entrance, 6 m, 6 o'clock. Verified, Riverside Hall."      |
| Hearing ("Echo")   | Deaf / hard of hearing  | "Siren-like sound, left. Inferred, microphone." (with a confidence)   |
| Touch/motor        | Limited mobility        | One-switch scanning, dwell selection, keyboard intents                |
| Smell (ScentGuard) | People who cannot smell | "Smoke risk level 3. Verified, Riverside Hall." with rules and freshness |
| Taste (TasteLens)  | Impaired taste          | Texture, spice, ingredients, and allergen alerts that never say "safe" |

## Quickstart

Requires **Node 20.19 or newer**.

```bash
npm install
npm run demo                 # open the printed URL; add  -- --open  to open a browser, -- --auto to play every scene
```

No microphone, camera, account, API key or network is needed.

| Command                       | What it does                                                                                         |
| ----------------------------- | ---------------------------------------------------------------------------------------------------- |
| `npm run demo`                | Starts the local SENSE server (with the simulated world) and serves the web app. Manual or `--auto`. |
| `npm run demo:verify`         | Headless, **offline** run of the whole six-scene scenario with assertions. Exit code 0 only if every one holds. |
| `npm run demo:cli`            | The same scenes as colored text, with provenance tags.                                               |
| `npm run verify`              | Typecheck + lint + format check + all tests + build.                                                 |
| `npm run test:coverage`       | Tests with a coverage summary.                                                                       |
| `npm run world-sim`           | Only the simulated world, over HTTP, one hostname per agent (`curl -H "Host: riverside-hall.sim" …`). |
| `npm run seed`                | Creates the local dev CA keys (gitignored) and prints the simulated world.                           |
| `npm run schemas`             | Exports JSON Schemas to `docs/schemas/`.                                                             |
| `npm run create-sense-agent`  | Scaffolds a publisher agent. See [docs/PUBLISH_AN_AGENT.md](docs/PUBLISH_AN_AGENT.md).               |

## Modes: what is real and what is not

|                                | Default                                              | Switch                                                                    |
| ------------------------------ | ---------------------------------------------------- | ------------------------------------------------------------------------- |
| **Network**                    | Everything works offline. One optional connectivity ping at startup. | `SENSE_OFFLINE=1` skips the ping.                                          |
| **World**                      | **SIMULATED WORLD**: `.sim` places, sensors, attackers | There is no live world mode.                                              |
| **Identity**                   | **ANS-modeled (simulated)**: real certificates, signatures and Merkle proofs against a local CA and log | `ANS_MODE=live` selects a typed stub that throws `NotConfigured`. It never fakes a live result. |
| **AI (vision, taste)**         | **MOCK AI**: recorded fixtures                       | `ANTHROPIC_API_KEY=…` (model via `SENSE_MODEL`, default `claude-sonnet-5`). Implemented and tested with a stubbed `fetch`; **not exercised against the live API**. |
| **Sounds**                     | Local DSP heuristics + a scripted soundscape         | Optional microphone in the browser (analysed on your device).            |
| **Webcam head/eye/hand input** | Not available in this build (no model files vendored) | Use switch scanning, keyboard intents, or dwell over any pointer stream.  |

The UI shows **SIMULATED WORLD**, the identity mode, the AI mode and offline status in the header at all
times. See [`.env.example`](.env.example) for every setting.

## Limitations notice

**SENSE is a prototype. It is not a medical device and not a certified safety system.** It must not be
relied on for life-safety decisions, including fire, air quality, allergens or navigation. Simulated
data is labelled "SIMULATED WORLD". Identity checks are "ANS-modeled" and are **not** ANS-compliant.
The interface is designed to a **WCAG 2.2 AA target** and has been checked with automated tools and a
keyboard-only walkthrough; it has **not** been certified or evaluated with disabled users. It makes no
claim of medical accuracy. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for what is and is not defended.

## Principles the code enforces

- **Never assert safety it cannot verify.** "No hazard reported by verified sources" yes; "the air is safe" never. Every percept passes a guard.
- **Fail loud.** Offline, stale or contradictory sources are reported plainly, with what SENSE falls back to.
- **Remote content is data, never instructions.** Strict schemas, signatures, whole-field sanitizing, a frozen policy.
- **Privacy by design.** The profile and declared allergens never leave the device; every message sent is in a visible Disclosure Log; no analytics.
- **Accessible by construction.** Keyboard operation, ARIA live regions, visible focus, contrast checked from the design tokens, reduced motion, 200% text, captions for all audio, haptic or visual equivalents for every alert.

## Repository map

```
packages/
  protocol/    schemas (Percept, Profile, Sense Card, messages), personas, sanitizer, safety guard
  identity/    ANS-modeled identity: CA, certificates, revocation, Merkle log, 7-step verification
  core/        SenseBroker, trust engine, rate limiter, disclosure gate, frozen policy
  providers/   LLM abstraction, Anthropic client, offline mock fixtures
  render/      percept -> speech / spatial audio / visual / haptic routing and renderers
  senses/      vision · hearing · scent · taste · touchless · crowd (a 64-line plugin)
apps/
  world-sim/   simulated publishers, attackers, timeline, HTTP view
  server/      local API + WebSocket + golden demo scenes
  web/         accessible React app (persona switcher, feed, Trust Inspector, Disclosure Log)
scripts/       demo, demo:verify, demo:cli, seed, schemas, create-sense-agent
docs/          the documents below
.sense/        working state of the autonomous build (progress, decisions, blockers)
```

## Documents

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, trust model, verification pipeline, extension points
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md): assets, adversaries, the eight attacks, honest limits
- [docs/SENSE_CARD_SPEC.md](docs/SENSE_CARD_SPEC.md): the Sense Card format and JSON Schema
- [docs/SCENTGUARD_RULES.md](docs/SCENTGUARD_RULES.md): the smoke-risk rules (illustrative thresholds)
- [docs/ANS_NOTES.md](docs/ANS_NOTES.md): what was read about ANS and how it shaped the design
- [docs/PUBLISH_AN_AGENT.md](docs/PUBLISH_AN_AGENT.md): publisher onboarding, with honest timing
- [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md): 3-minute and 60-second demo scripts
- [docs/PITCH.md](docs/PITCH.md) and [docs/ROADMAP.md](docs/ROADMAP.md)
- [FINAL_REPORT.md](FINAL_REPORT.md): what was built, test results, known gaps

## Development

`npm run verify` must pass before every commit. Libraries are consumed as TypeScript source (no
per-package build); `tsc --noEmit` type-checks everything, `tsx` runs Node processes, Vite bundles the
web app. Tests are deterministic: seeded randomness, injected clocks, no real network. Safety
invariants live in `packages/core/test/invariants.test.ts`; do not weaken an assertion to get green.

License: MIT.
