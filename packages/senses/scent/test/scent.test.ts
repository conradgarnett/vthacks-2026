import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { ManualClock, containsAssurance, sequentialIds, type Tier } from '@sense/protocol';
import { RULES, ScentGuard, assess, ruleFor, urgencyFor, type ScentAlarm, type ScentReading, type ScentSource } from '../src';

const T0 = Date.parse('2026-01-15T10:00:00.000Z');
const iso = (ms: number) => new Date(ms).toISOString();

const reading = (kind: ScentReading['kind'], value: number, ageS = 0, over: Partial<ScentReading> = {}): ScentReading => ({
  sensorId: `${kind}-1`,
  kind,
  value,
  unit: kind === 'smoke' ? '%obs/m' : kind === 'co' ? 'ppm' : kind === 'aqi' ? 'AQI' : kind === 'voc' ? 'ppb' : 'ug/m3',
  measuredAt: iso(T0 - ageS * 1000),
  label: kind === 'smoke' ? 'East stairwell smoke' : kind,
  location: { x: 14, y: 8 },
  ...over,
});

const hall = (readings: ScentReading[], over: Partial<ScentSource> = {}): ScentSource => ({
  fqdn: 'riverside-hall.sim',
  label: 'Riverside Hall',
  tier: 'VERIFIED',
  simulated: true,
  agentVersion: '1.0.0',
  verifiedAt: iso(T0),
  evidence: ['7/7 identity checks passed (ANS-modeled)'],
  readings,
  maxAgeSeconds: 60,
  ...over,
});

const city = (aqi: number, over: Partial<ScentSource> = {}): ScentSource =>
  hall([reading('aqi', aqi, 0, { label: 'Riverside district air quality index', sensorId: 'aqi-1' })], {
    fqdn: 'city-air.sim',
    label: 'City Air Network',
    maxAgeSeconds: 900,
    ...over,
  });

const fire = (over: Partial<ScentAlarm> = {}): ScentAlarm => ({
  fqdn: 'riverside-hall.sim',
  label: 'Riverside Hall',
  tier: 'VERIFIED',
  active: true,
  ...over,
});
const A = (sources: ScentSource[], alarms: ScentAlarm[] = []) => assess({ sources, alarms, nowMs: T0 });

describe('rule table', () => {
  it('fires at the threshold and not below, and picks the highest rule', () => {
    expect(ruleFor('smoke', 'building', 0.49)).toBeUndefined();
    expect(ruleFor('smoke', 'building', 0.5)?.id).toBe('S1');
    expect(ruleFor('smoke', 'building', 4.99)?.id).toBe('S2');
    expect(ruleFor('smoke', 'building', 5)?.id).toBe('S3');
    expect(ruleFor('smoke', 'building', 500)?.id).toBe('S4');
    expect(ruleFor('co', 'building', 9.9)).toBeUndefined();
    expect(ruleFor('co', 'building', 100)?.id).toBe('C4');
    expect(ruleFor('aqi', 'regional', 149)?.id).toBe('A1');
    expect(ruleFor('smoke', 'regional', 100)).toBeUndefined(); // scope must match
  });

  it('docs/SCENTGUARD_RULES.md matches the rules in code', () => {
    const md = readFileSync(join(import.meta.dirname, '../../../../docs/SCENTGUARD_RULES.md'), 'utf8');
    const rows = [...md.matchAll(/^\|\s*([A-Z]\d)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*([\d.]+)\s*\|\s*(\S+)\s*\|\s*(\d)\s*\|/gm)].map((m) => ({
      id: m[1],
      kind: m[2],
      scope: m[3],
      min: Number(m[4]),
      unit: m[5],
      level: Number(m[6]),
    }));
    expect(rows).toHaveLength(RULES.length);
    for (const r of RULES)
      expect(rows).toContainEqual({ id: r.id, kind: r.kind, scope: r.scope, min: r.min, unit: r.unit, level: r.level });
    expect(md).toMatch(/illustrative/i);
  });
});

describe('assessment', () => {
  it('baseline: fresh verified readings below every threshold is level 0, and says only that', () => {
    const a = A([hall([reading('smoke', 0.1), reading('co', 1, 0, { sensorId: 'co-1' })])]);
    expect(a.level).toBe(0);
    expect(a.tier).toBe('VERIFIED');
    expect(a.currentUnknown).toBe(false);
  });

  it('escalates 1 -> 2 -> 3 as the smoke reading rises, naming the rules', () => {
    expect(A([hall([reading('smoke', 0.8)])]).level).toBe(1);
    const l2 = A([hall([reading('smoke', 2.6), reading('co', 8, 0, { sensorId: 'co-1' })])]);
    expect(l2.level).toBe(2);
    const l3 = A([hall([reading('smoke', 6.2), reading('co', 19, 0, { sensorId: 'co-1' })])]);
    expect(l3.level).toBe(3);
    expect(l3.hits.map((h) => h.rule.id).sort()).toEqual(['C2', 'S3']);
    expect(l3.tier).toBe('VERIFIED');
    expect(l3.driver?.fqdn).toBe('riverside-hall.sim');
  });

  it('a VERIFIED active fire alarm forces at least level 3, and level 4 with high smoke (X1, X2)', () => {
    const noSmoke = A([hall([reading('smoke', 0.1)])], [fire()]);
    expect(noSmoke.level).toBe(3);
    expect(noSmoke.combos).toEqual(['X1']);
    const withSmoke = A([hall([reading('smoke', 6.2)])], [fire()]);
    expect(withSmoke.level).toBe(4);
    expect(withSmoke.combos).toEqual(['X1', 'X2']);
  });

  it('an unverified or inactive alarm does not trigger the combination rules', () => {
    expect(A([hall([reading('smoke', 0.1)])], [fire({ tier: 'UNVERIFIED' })]).level).toBe(0);
    expect(A([hall([reading('smoke', 0.1)])], [fire({ active: false })]).level).toBe(0);
  });

  it('regional data adds context and can raise the level to at most 2', () => {
    const a = A([hall([reading('smoke', 0.1)]), city(400)]);
    expect(a.level).toBe(2);
    expect(a.context.join(' ')).toMatch(/does not describe air inside a building/);
    expect(a.context.join(' ')).toMatch(/does not mean outdoor air is better/);
    expect(A([hall([reading('smoke', 6.2)]), city(160)]).level).toBe(3); // building data dominates
  });

  it('stale readings keep the last known level but are UNVERIFIED and flagged unknown-now', () => {
    const a = A([hall([reading('smoke', 6.2, 120)])]);
    expect(a.level).toBe(3);
    expect(a.tier).toBe('UNVERIFIED');
    expect(a.currentUnknown).toBe(true);
    expect(a.hits[0]).toMatchObject({ stale: true, ageSeconds: 120 });
    expect(a.cautions.join(' ')).toMatch(/last known, not current/);
  });

  it('missing data is unknown, never an all-clear, and never VERIFIED', () => {
    const none = A([]);
    expect(none).toMatchObject({ level: 0, tier: 'UNVERIFIED', currentUnknown: true });
    expect(none.cautions.join(' ')).toMatch(/No building air-quality data/);
    const staleZero = A([hall([reading('smoke', 0.1, 300)])]);
    expect(staleZero).toMatchObject({ level: 0, tier: 'UNVERIFIED', currentUnknown: true });
  });

  it('readings from an unverified source are UNVERIFIED even when fresh', () => {
    const a = A([hall([reading('smoke', 6.2)], { tier: 'UNVERIFIED' })]);
    expect(a.level).toBe(3);
    expect(a.tier).toBe('UNVERIFIED');
    expect(a.currentUnknown).toBe(true);
  });

  it('is a pure function of its inputs', () => {
    const s = [hall([reading('smoke', 2.6)])];
    expect(A(s)).toEqual(A(s));
  });
});

describe('urgency', () => {
  it('only a VERIFIED level 4 is life-safety urgency 4', () => {
    expect([0, 1, 2, 3].map((l) => urgencyFor(l, 'VERIFIED'))).toEqual([1, 2, 3, 3]);
    expect(urgencyFor(4, 'VERIFIED')).toBe(4);
    expect(urgencyFor(4, 'UNVERIFIED')).toBe(3);
  });
});

describe('scene 5: ScentGuard percepts', () => {
  function make() {
    const clock = new ManualClock(T0);
    const guard = new ScentGuard({ clock, nextId: sequentialIds('p'), getPose: () => ({ position: { x: 0, y: 6 }, headingDeg: 0 }) });
    const feed = (smoke: number, co = 1, ageS = 0) => ({
      sources: [
        hall([
          reading('smoke', smoke, ageS),
          reading('co', co, ageS, { sensorId: 'co-1', label: 'Lobby carbon monoxide', location: { x: 0, y: 6 } }),
        ]),
        city(42),
      ],
      alarms: [] as ScentAlarm[],
    });
    return { clock, guard, feed };
  }

  it('escalates level 1 -> 2 -> 3, each alert citing its evidence and freshness', () => {
    const { guard, feed } = make();
    expect(guard.update(feed(0.1))).toHaveLength(1); // first data: baseline status
    const l1 = guard.update(feed(0.8))[0];
    const l2 = guard.update(feed(2.6, 8))[0];
    const l3 = guard.update(feed(6.2, 19))[0];
    expect([l1, l2, l3].map((p) => p?.short)).toEqual([
      'Smoke risk level 1. Verified, Riverside Hall.',
      'Smoke risk level 2. Verified, Riverside Hall.',
      'Smoke risk level 3. Verified, Riverside Hall.',
    ]);
    expect([l1, l2, l3].map((p) => p?.urgency)).toEqual([2, 3, 3]);
    expect(l3?.kind).toBe('alert');
    expect(l3?.long).toMatch(/East stairwell smoke: 6.2 %obs\/m, 0s old, fires rule S3/);
    expect(l3?.long).toMatch(/threshold 5 %obs\/m/);
    expect(l3?.long).toMatch(/rule C2/);
    expect(l3?.long).toMatch(/Level 3 of 4 \(Severe\), rising/);
    expect(l3?.long).toMatch(/not a measurement of the air where you are and not a safety guarantee/);
    expect(l3?.provenance).toMatchObject({ tier: 'VERIFIED', source: 'riverside-hall.sim', sourceLabel: 'Riverside Hall' });
    expect(l3?.provenance.evidence.join(' ')).toMatch(/rule S3: 6.2 %obs\/m >= 5, age 0s/);
    expect(l3?.spatial?.clockPosition).toBe(3);
    expect(l3?.safety).toBe(true);
    expect(l3?.simulated).toBe(true);
    expect(l3?.actions?.[0]?.id).toBe('ack');
    expect(guard.status()).toMatchObject({ level: 3, tier: 'VERIFIED', rules: ['S3', 'C2'] });
  });

  it('does not repeat itself when nothing changed', () => {
    const { guard, feed } = make();
    guard.update(feed(2.6));
    expect(guard.update(feed(2.6))).toEqual([]);
  });

  it('going stale downgrades to UNVERIFIED and says so; it never assumes things improved', () => {
    const { guard, feed, clock } = make();
    guard.update(feed(6.2, 19));
    clock.advance(120_000);
    const stale = guard.update(
      feed(6.2, 19, 0).sources.length ? { sources: [hall([reading('smoke', 6.2, 120)]), city(42)], alarms: [] } : feed(6.2),
    )[0];
    expect(stale?.short).toBe('Smoke risk level 3, last known. Unverified, Riverside Hall.');
    expect(stale?.provenance.tier).toBe('UNVERIFIED');
    expect(stale?.long).toMatch(/last known, not current/);
    expect(stale?.long).toMatch(/does not assume conditions have improved/);
    expect(stale?.urgency).toBe(3);
  });

  it('reports a reduction as reduced, not as cleared', () => {
    const { guard, feed } = make();
    guard.update(feed(6.2, 19));
    const down = guard.update(feed(0.8))[0];
    expect(down?.short).toBe('Smoke risk level 1, reduced. Verified, Riverside Hall.');
    expect(down?.long).toMatch(/reduced/);
    expect(down?.long).not.toMatch(/cleared|resolved/);
  });

  it('fresh verified level 0 is reported as "no elevated reading reported", with the caveat', () => {
    const { guard, feed } = make();
    const first = guard.update(feed(0.1))[0];
    expect(first?.short).toBe('No elevated air reading reported. Verified, Riverside Hall.');
    expect(first?.kind).toBe('status');
    expect(first?.long).toMatch(/not a safety guarantee/);
  });

  it('no data at all says nothing until data has ever arrived, then says unknown', () => {
    const { guard } = make();
    expect(guard.update({ sources: [], alarms: [] })).toEqual([]);
    expect(guard.status()).toMatchObject({ level: 0, tier: 'UNVERIFIED' });
    expect(guard.status()?.summary).toMatch(/unknown/);
  });

  it('implements the SenseModule seam', async () => {
    const { guard, feed } = make();
    const mod = guard.module(async function* () {
      yield feed(0.8);
    });
    const out = [];
    for await (const p of mod.produce()) out.push(p);
    expect(mod.id).toBe('smell');
    expect(out[0]?.sense).toBe('smell');
  });
});

describe('INVARIANTS (property-based)', () => {
  const tiers = ['VERIFIED', 'UNVERIFIED'] as const;
  const readingArb = fc.record({
    kind: fc.constantFrom('smoke', 'co', 'voc', 'aqi', 'pm25') as fc.Arbitrary<ScentReading['kind']>,
    value: fc.double({ min: 0, max: 500, noNaN: true }),
    ageS: fc.integer({ min: 0, max: 3600 }),
  });

  it('stale or unverified evidence is never VERIFIED, and missing data is always unknown', () => {
    fc.assert(
      fc.property(fc.array(readingArb, { maxLength: 6 }), fc.constantFrom(...tiers), fc.boolean(), (rs, tier, alarmActive) => {
        const src = hall(
          rs.map((r, i) => reading(r.kind, r.value, r.ageS, { sensorId: `s${i}` })),
          { tier },
        );
        const a = A(rs.length ? [src] : [], alarmActive ? [fire()] : []);
        const allFresh = rs.length > 0 && rs.every((r) => r.ageS <= 60);
        const anyFreshBuilding = rs.some((r) => (r.kind === 'smoke' || r.kind === 'co' || r.kind === 'voc') && r.ageS <= 60);
        if (tier === 'UNVERIFIED' && !alarmActive) expect(a.tier).not.toBe('VERIFIED');
        if (rs.length === 0) expect(a.currentUnknown).toBe(true);
        if (!anyFreshBuilding || tier !== 'VERIFIED') expect(a.currentUnknown).toBe(true);
        if (a.level === 0 && !(tier === 'VERIFIED' && anyFreshBuilding)) expect(a.tier).not.toBe('VERIFIED');
        void allFresh;
      }),
      { numRuns: 300 },
    );
  });

  it('a higher smoke reading never lowers the level (monotonic)', () => {
    fc.assert(
      fc.property(fc.double({ min: 0, max: 50, noNaN: true }), fc.double({ min: 0, max: 50, noNaN: true }), (x, y) => {
        const [lo, hi] = x <= y ? [x, y] : [y, x];
        expect(A([hall([reading('smoke', hi)])]).level).toBeGreaterThanOrEqual(A([hall([reading('smoke', lo)])]).level);
      }),
      { numRuns: 200 },
    );
  });

  it('no percept text ever contains assurance wording, for any input', () => {
    fc.assert(
      fc.property(fc.array(readingArb, { maxLength: 5 }), fc.constantFrom(...tiers), (rs, tier) => {
        const clock = new ManualClock(T0);
        const guard = new ScentGuard({ clock, nextId: sequentialIds('p'), getPose: () => ({ position: { x: 0, y: 6 }, headingDeg: 0 }) });
        const ps = guard.update({
          sources: rs.length
            ? [
                hall(
                  rs.map((r, i) => reading(r.kind, r.value, r.ageS, { sensorId: `s${i}` })),
                  { tier },
                ),
                city(30),
              ]
            : [],
          alarms: [],
        });
        for (const p of ps) {
          expect(containsAssurance(p.short), p.short).toBe(false);
          expect(containsAssurance(p.long ?? ''), p.long).toBe(false);
          expect(p.provenance.tier as Tier).not.toBe('REJECTED');
          if (p.urgency === 4) expect(p.provenance.tier).toBe('VERIFIED');
        }
      }),
      { numRuns: 200 },
    );
  });
});
