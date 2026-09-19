# Decisions

Format: `D<n>` — decision — rationale. Newest at the bottom.

- D1 — Network check: `https://nodejs.org` and the npm registry were reachable at build start (2026-09-18), so dependencies were installed online. All runtime features still work offline after install; network-dependent features have offline fallbacks.
- D2 — Node toolchain: the machine had no Node. Rule 2.5 forbids global installs, so a portable Node 24.21.0 LTS (SHA-256 verified against nodejs.org SHASUMS256) was unpacked into `.tools/node` inside the project (gitignored). Users with their own Node >= 20.19 do not need it.
- D3 — TypeScript pinned to `~6.0.3`: typescript-eslint 8.70 declares peer `typescript <6.1`, and TypeScript 7 (native port) is not supported by it. Type-check stays with `tsc`.
- D4 — ESLint pinned to 9.x (not 10): `eslint-plugin-jsx-a11y` (needed to lint the a11y of the app) only supports ESLint <= 9. ESLint 9.39.x is flagged "no longer supported" by npm; acceptable for a prototype, revisit when jsx-a11y supports 10.
- D5 — Libraries are consumed as TypeScript source (workspace `exports` -> `src/index.ts`). No per-package build step. `tsc --noEmit` is the type check, `tsx` runs Node processes, Vite bundles the web app. `npm run build` therefore builds the web app (which type-checks and bundles every browser-side package).
- D6 — Zod 4 is used; its built-in `z.toJSONSchema` is used for JSON Schema export (no extra dependency).
- D7 — Certificates via `@peculiar/x509` (WebCrypto, ECDSA P-256) rather than node-forge: maintained, TypeScript-native, runs on Node and browser.
- D8 — Prettier ignores Markdown to avoid formatting churn in docs; ESLint ignores `docs/`.
- D9 — `Percept.short` is capped at 10 words. Assumption (not measured): 2.8 words/s at speech rate 1.0 and alerts spoken at >= 1.8x, so 10 words ~ 2 s. Safety percepts must contain the tier word and source label inside `short` (enforced in the schema).
- D10 — Agent payload schemas use `strictObject`: unknown keys make the payload REJECTED rather than being silently stripped, so smuggled fields like `systemPrompt` become visible security events.
- D11 — Personas, situational presets and the profile builder live in `@sense/protocol` (pure data + validation) so core, render, server and web share one definition.
- D12 — ANS research used 3 fetches (limit was ~10); enough for the design. ansinfo.ai describes ANS as GoDaddy-led open source, not Linux-Foundation-governed, so docs say so. Proof-of-control (ACME) is not modeled; documented as a gap.
- D13 — Verification stops at the first failing step (later steps shown as "skipped" in the inspector); an *unavailable* step (log/CRL down) yields UNVERIFIED and later steps still run. Unlogged agent = REJECTED (log reachable, entry absent); unreachable log = UNVERIFIED.
- D14 — `@peculiar/x509` needs `reflect-metadata` (tsyringe); added as an identity dependency.
- D15 — Append-only property is checked by pinning the last-seen signed tree head and comparing `rootAt(size)`; RFC 6962 consistency proofs are roadmap.
- D16 — "Domains" in the sim = hostname map: one in-process `SimTransport` maps each `.sim` FQDN to its agent (messages take a JSON round trip so agents and broker never share objects). An HTTP wrapper exposes the same agents with Host-header virtual hosting for curl/inspection. The broker uses the in-process transport; an HTTP broker transport is not built (would also need a remote registry client). Documented as a gap.
- D17 — Attackers are inert until `activateAttackers()`; the impersonator gets a valid CA-issued cert for a different FQDN, the spoofer's certs come from an untrusted rogue CA and its record is injected into discovery via a poisoned-registry helper (`publishRecordUnchecked`, models DNS/registry poisoning).
- D18 — Alarm-feed subscriptions get a 10 s heartbeat from the sim so that silence is detectable: a feed that goes quiet past its declared freshness is downgraded to UNVERIFIED instead of being assumed unchanged.
- D19 — An earlier vitest run appeared to hang ~15 minutes (environment stall; identical tests then ran in ~20 ms each). No code change needed; commands now use `timeout`.
- D20 — Sanitizer drops the ENTIRE field when instruction-like or reassuring text is detected (no partial editing), and logs INJECTION_NEUTRALIZED. Remote free text that survives is only ever shown quoted as a "publisher note (unverified text, shown as data)".
- D21 — Unsolicited pushes are never routed, even from a verified agent. The broker verifies the sender only to report who it was. Alarm state is accepted only from subscribed feeds and is keyed per source, so one agent cannot clear another's alarm.
- D22 — An UNVERIFIED source's alarm is still surfaced (fail loud) but urgency is capped at 3; life-safety urgency 4 requires VERIFIED. A stale VERIFIED feed is therefore surfaced as UNVERIFIED rather than dropped.
- D23 — Disclosure gate: generic profile words ("profile", "allergens", ...) are blocked as JSON keys only, because they legitimately appear inside capability ids; profile VALUES (allergens, name, id, persona) are blocked anywhere as whole words.
- D24 — Tooling quirk: `\uXXXX` sequences typed in tool inputs were decoded into literal characters; invisible-character regexes are therefore generated with `chr(92)` and verified by ESLint's no-irregular-whitespace.
- D25 — The server is the user's LOCAL SENSE process (profile lives there, never sent to remote agents). The browser receives the profile to compute render plans; the broker's outbound gate protects what leaves the device.
- D26 — Web tests run the real SenseContext + Fastify (via inject) behind the real React app instead of mocking the API, so UI tests exercise the real broker. axe runs with color-contrast disabled (jsdom has no layout); contrast is instead computed from the CSS design tokens in a separate test.
- D27 — Screen-reader vs SENSE voice: live regions always update; SENSE's own voice can be muted in the header (documented in README) so a screen-reader user does not hear everything twice.
