# ScentGuard rules

ScentGuard helps people who cannot smell by turning **verified sensor data** into a risk level with
an explanation. It is a transparent rule table, not a model.

> **Every threshold below is illustrative.** The numbers were chosen to make escalation
> demonstrable. They are **not** derived from any safety standard, building code or medical
> guidance, and SENSE must not be used to decide whether it is safe to stay somewhere.

The authoritative table is `packages/senses/scent/src/rules.ts`. A test fails if this document and
the code disagree.

## Single-sensor rules

A rule fires when a reading is **at or above** the threshold. The highest rule that fires for a
reading counts.

| Rule | Kind  | Scope    | Threshold | Unit   | Level | Meaning                              |
| ---- | ----- | -------- | --------- | ------ | ----- | ------------------------------------ |
| S1   | smoke | building | 0.5       | %obs/m | 1     | Smoke reading above background       |
| S2   | smoke | building | 2         | %obs/m | 2     | Smoke reading clearly elevated       |
| S3   | smoke | building | 5         | %obs/m | 3     | Smoke reading high                   |
| S4   | smoke | building | 10        | %obs/m | 4     | Smoke reading very high              |
| C2   | co    | building | 10        | ppm    | 2     | Carbon monoxide elevated             |
| C3   | co    | building | 35        | ppm    | 3     | Carbon monoxide high                 |
| C4   | co    | building | 100       | ppm    | 4     | Carbon monoxide very high            |
| V1   | voc   | building | 500       | ppb    | 1     | Volatile organic compounds elevated  |
| V2   | voc   | building | 2000      | ppb    | 2     | Volatile organic compounds high      |
| A1   | aqi   | regional | 100       | AQI    | 1     | Regional air quality index elevated  |
| A2   | aqi   | regional | 150       | AQI    | 2     | Regional air quality index high      |
| P1   | pm25  | regional | 35        | ug/m3  | 1     | Regional fine particles elevated     |
| P2   | pm25  | regional | 55        | ug/m3  | 2     | Regional fine particles high         |

## Combination rules

- **X1**: a fire alarm is **active** from a **VERIFIED** source: at least level 3, even with no smoke reading.
- **X2**: a fire alarm is active from a VERIFIED source **and** a building smoke reading is at least the S3 threshold: level 4.

An alarm from an unverified or rejected source never triggers X1 or X2.

## Levels and urgency

| Level | Label                        | Urgency (percept) |
| ----- | ---------------------------- | ----------------- |
| 0     | No elevated reading reported | 1 (status)        |
| 1     | Elevated                     | 2                 |
| 2     | High                         | 3                 |
| 3     | Severe                       | 3                 |
| 4     | Critical                     | 4 if VERIFIED, else 3 |

Level **0 never means "safe"**. It means only: _no elevated reading was reported by fresh, verified
building sensors_. It is worded that way in the app.

## Regional data

City-level data (AQI, PM2.5) can raise the level to **at most 2** on its own, because it does not
describe the air inside a building. It is shown as context and never as a statement that outdoor air
is better or worse than indoor air.

## Missing, stale and unverified data

- **Missing data** produces "air conditions unknown", never an all-clear.
- **Stale data** (older than the publisher's declared freshness limit) keeps the **last known** level,
  is downgraded to `UNVERIFIED`, and says how old it is. SENSE does not assume things improved.
- **Unverified sources** (identity checks incomplete) are `UNVERIFIED`; their readings are shown but
  cannot produce life-safety urgency 4.

## What each alert explains

Every ScentGuard percept lists: the level, which rule IDs fired, each reading with its value, unit,
age in seconds and the rule threshold, the reporting source and its trust tier, any combination rule,
regional context, cautions about unknown or stale data, and the reminder that the level is a
data-based estimate and not a guarantee.
