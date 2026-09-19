# Sense Card specification (draft 0.1)

A **Sense Card** is "alt text for physical reality": a publisher's machine-readable, signed
description of what its place or service can tell a SENSE agent, and how far that information can
be trusted.

> **Status:** this is SENSE's own draft format. It is **not** an ANS, MCP or A2A standard, and no
> standards body has reviewed it. See `docs/ROADMAP.md` for the standards-engagement plan.

The authoritative definition is the Zod schema in `packages/protocol/src/sensecard.ts`. The JSON
Schema (draft 2020-12) generated from it lives in
[`docs/schemas/sense-card.schema.json`](schemas/sense-card.schema.json) (`npm run schemas`).

## What a card declares

| Field                                 | Meaning                                                                                       |
| ------------------------------------- | --------------------------------------------------------------------------------------------- |
| `schema`                              | Always `sense-card/0.1`.                                                                      |
| `agent.fqdn`                          | The domain the agent's identity is anchored to. Must equal the FQDN in its certificates.      |
| `agent.version`                       | Version string. Each version is immutable once registered.                                    |
| `agent.digest`                        | `sha256:<hex>` of the agent's code and metadata for this version (silent-swap detection).     |
| `simulated`                           | Publisher-declared. Clients label simulated sources themselves regardless.                    |
| `capabilities[].id`                   | One of the capability ids below.                                                              |
| `capabilities[].basis`                | `direct-sensor`, `staff-entered`, `inferred` or `static`: where the data comes from.          |
| `capabilities[].freshness`            | `maxAgeSeconds`: data older than this is downgraded to `UNVERIFIED`. `null` means static.     |
| `capabilities[].scopes`               | Scopes a client must request. Clients ask for the minimum they need.                          |
| `capabilities[].safetyCritical`       | If true, results are presented with provenance in the primary message and never as an all-clear. |
| `capabilities[].subscribable`         | Supports push subscriptions (alerts).                                                         |

## Capabilities

| id                       | Payload (see `packages/protocol/src/payloads.ts`)                                   | Typical basis   |
| ------------------------ | ----------------------------------------------------------------------------------- | --------------- |
| `indoor-map`             | Nodes (entrance, exit, stairs, lift, room, ...) and weighted edges.                 | static          |
| `alarm-feed`             | Active/cleared/test alarms with zone and location. Safety-critical, subscribable.   | direct-sensor   |
| `air-quality`            | Sensor readings (smoke, CO, VOC, PM2.5, AQI) with unit and `measuredAt`.           | direct-sensor   |
| `menu-allergens`         | Menu items with ingredients, `contains` and `mayContain` allergens, preparation.    | staff-entered   |
| `arrivals`               | Transit arrivals with ETA and accessibility flag.                                   | direct-sensor   |
| `accessibility-features` | Step-free routes, hearing loops, tactile paving, visual alarms, ...                 | staff-entered   |
| `device-control`         | Devices (e.g. elevator) and permitted actions (e.g. `call`).                        | direct-sensor   |

## Signing and verification

The card is wrapped as `{ card, signature, signerFingerprint }`. The signature is ECDSA P-256 /
SHA-256 over the **canonical JSON** of `card` (object keys sorted recursively), made with the same
identity key whose certificate is bound to `agent.fqdn`, `agent.version` and `agent.digest`.

Verification step 7 in the pipeline (`docs/ARCHITECTURE.md`) checks that the card:

1. is schema-valid,
2. names the same FQDN, version and digest as the identity certificate, and
3. carries a valid signature from that identity certificate.

A card that fails any of these makes the agent `REJECTED`.

## Example

```json
{
  "schema": "sense-card/0.1",
  "agent": {
    "fqdn": "riverside-hall.sim",
    "version": "1.0.0",
    "digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    "name": "Riverside Hall (simulated)",
    "description": "Community hall with a fire panel, indoor map and air-quality sensors."
  },
  "simulated": true,
  "capabilities": [
    {
      "id": "alarm-feed",
      "summary": "Fire panel alarm state, pushed when it changes.",
      "basis": "direct-sensor",
      "freshness": { "maxAgeSeconds": 30 },
      "scopes": ["alarms:read"],
      "safetyCritical": true,
      "safetyNote": "Absence of an alarm is not a statement that the building is safe.",
      "subscribable": true
    },
    {
      "id": "indoor-map",
      "summary": "Rooms, exits and corridors with distances.",
      "basis": "static",
      "freshness": { "maxAgeSeconds": null },
      "scopes": ["map:read"],
      "safetyCritical": false
    }
  ],
  "issuedAt": "2026-01-15T10:00:00.000Z"
}
```

## Honest limits

- A signed Sense Card proves **who published it**, not that the content is correct. A verified
  agent can still be wrong, stale or hostile, which is why every payload is schema-validated,
  sanitized and treated as data (see `docs/THREAT_MODEL.md`).
- `freshness` and `basis` are the publisher's claims. SENSE enforces freshness against the
  payload's own `measuredAt` / `asOf` timestamps, not against the card alone.
