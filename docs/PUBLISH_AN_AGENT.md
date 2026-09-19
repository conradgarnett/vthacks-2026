# Publish an accessibility agent

A business, building or service publishes what its place already knows as a SENSE agent, with a Sense
Card describing it. In this repository that means the **simulated** registry only: nothing here
publishes to a real ANS registry.

## The 10-minute claim, measured honestly

`npm run create-sense-agent` scaffolds a working agent, generates the Sense Card, registers it, and runs
the same seven verification steps a SENSE user's device runs.

| Step                                                            | Measured (machine time, this build) |
| --------------------------------------------------------------- | ----------------------------------- |
| Generate the agent, card and self-check                         | about 0.8 s                         |
| Register and verify, including a fresh `tsx` process start      | about 1.3 s (verification itself 38 ms) |
| **Total**                                                       | **about 2.2 s**                     |

What this does **not** measure: the human work of writing real data into the provider functions,
deciding freshness limits and scopes, and testing it. We did not time that with real publishers, so the
"in ten minutes" claim is **supported for the scaffolding and unproven for a real business**.

## Steps

```bash
npm run create-sense-agent -- my-cafe.sim --name "My Cafe" \
  --capability menu-allergens --capability accessibility-features --covers riverside
npx tsx agents/my-cafe.sim/run.ts     # PASS step 1 … PASS step 7, then: my-cafe.sim: VERIFIED
```

It writes `agents/my-cafe.sim/`:

- `sense-card.json`: identity, capabilities with `basis`, `freshness`, `scopes`, `safetyCritical`. Edit names and limits here.
- `agent.ts`: the **providers**. Return your real data; every payload is validated against the schema in `@sense/protocol`, so invalid data is rejected rather than shown.
- `run.ts`: registers into an in-memory simulated registry and verifies.
- `README.md`.

Choose capabilities from: `indoor-map`, `alarm-feed`, `air-quality`, `menu-allergens`, `arrivals`,
`accessibility-features`, `device-control`. Safety-critical capabilities get a default
`safetyNote`: _absence of a warning is not a statement that anything is safe_.

## What a publisher should know

- **Identity is not trust in content.** SENSE validates and sanitizes everything you send. Free text that looks like an instruction or a reassurance is dropped and logged.
- **Freshness matters.** Data older than the limit you declare is shown as `UNVERIFIED`. For live feeds send heartbeats so silence can be detected.
- **Versions are immutable.** Changing the code changes its digest; re-register a new version rather than swapping code under the same one (that is the "silent code swap" SENSE rejects).
- **Minimum scope.** Requests carry only the scope a capability declares.
- See [SENSE_CARD_SPEC.md](SENSE_CARD_SPEC.md) for the card format.
