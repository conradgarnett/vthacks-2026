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
