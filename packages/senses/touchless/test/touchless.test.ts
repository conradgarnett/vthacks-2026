import fc from 'fast-check';
import { describe, expect, it, vi } from 'vitest';
import {
  DwellGazeDriver,
  DwellSelector,
  IntentController,
  KEY_INTENTS,
  KeyboardDriver,
  ManualScheduler,
  ScriptedDriver,
  SwitchScanDriver,
  TremorFilter,
  WebcamHeadDriver,
  applyCalibration,
  fitCalibration,
  realScheduler,
  type ActivationContext,
  type IntentEvent,
  type KeyEventLike,
  type Rect,
  type Target,
} from '../src';

const RECTS: Rect[] = [
  { id: 'ack', x: 100, y: 100, w: 100, h: 60 },
  { id: 'deaf', x: 300, y: 100, w: 100, h: 60 },
];

describe('tremor filter', () => {
  it('ignores movement inside the dead band and follows deliberate movement', () => {
    const f = new TremorFilter(0.5, 8);
    expect(f.update({ x: 100, y: 100 })).toEqual({ x: 100, y: 100 });
    expect(f.update({ x: 104, y: 97 })).toEqual({ x: 100, y: 100 }); // tremor
    const moved = f.update({ x: 200, y: 100 });
    expect(moved.x).toBeGreaterThan(100);
    expect(moved.x).toBeLessThan(200); // smoothed, not a jump
    f.reset();
    expect(f.update({ x: 5, y: 5 })).toEqual({ x: 5, y: 5 });
  });

  it('shakes of any size within the dead band never move the filtered point (property)', () => {
    fc.assert(
      fc.property(
        fc.array(fc.tuple(fc.double({ min: -5, max: 5, noNaN: true }), fc.double({ min: -5, max: 5, noNaN: true })), {
          minLength: 1,
          maxLength: 60,
        }),
        (jitter) => {
          const f = new TremorFilter(0.4, 8);
          f.update({ x: 50, y: 50 });
          for (const [dx, dy] of jitter) expect(f.update({ x: 50 + dx, y: 50 + dy })).toEqual({ x: 50, y: 50 });
        },
      ),
    );
  });
});

describe('dwell selector', () => {
  const still = (sel: DwellSelector, x: number, y: number, fromMs: number, toMs: number, stepMs = 50) => {
    const events = [];
    for (let t = fromMs; t <= toMs; t += stepMs) events.push(...sel.update({ x, y, t }));
    return events;
  };

  it('activates a target after the dwell time, exactly once', () => {
    const sel = new DwellSelector(1000);
    sel.setTargets(RECTS);
    const ev = still(sel, 150, 130, 0, 2500);
    expect(ev.filter((e) => e.type === 'enter')).toEqual([{ type: 'enter', id: 'ack' }]);
    expect(ev.filter((e) => e.type === 'activate')).toEqual([{ type: 'activate', id: 'ack' }]);
    const act = ev.findIndex((e) => e.type === 'activate');
    const progress = ev.filter((e) => e.type === 'progress');
    expect(progress.length).toBeGreaterThan(5);
    expect(act).toBeGreaterThan(5);
  });

  it('a quick pass never activates (false-activation guard)', () => {
    const sel = new DwellSelector(1000);
    sel.setTargets(RECTS);
    const ev = [...still(sel, 150, 130, 0, 400), ...still(sel, 600, 500, 450, 900)];
    expect(ev.some((e) => e.type === 'activate')).toBe(false);
    expect(ev.filter((e) => e.type === 'leave')).toEqual([{ type: 'leave', id: 'ack' }]);
  });

  it('tremor inside the target and slight drift past its edge do not break the dwell (hysteresis)', () => {
    const sel = new DwellSelector(800, 12);
    sel.setTargets(RECTS);
    const ev = [];
    // Start well inside, then drift to the edge and hover just past it with tremor.
    for (let t = 0; t <= 200; t += 40) ev.push(...sel.update({ x: 150, y: 130, t }));
    for (let t = 240; t <= 1400; t += 40)
      ev.push(...sel.update({ x: 203 + (t % 80 === 0 ? 4 : -2), y: 130 + (t % 120 === 0 ? -5 : 4), t }));
    expect(ev.some((e) => e.type === 'activate' && e.id === 'ack')).toBe(true);
    expect(ev.filter((e) => e.type === 'leave')).toEqual([]);
  });

  it('the dwell time is adjustable and clamped', () => {
    const sel = new DwellSelector(1000);
    sel.setTargets(RECTS);
    sel.setDwellMs(300);
    expect(still(sel, 150, 130, 0, 400).some((e) => e.type === 'activate')).toBe(true);
    sel.setDwellMs(1);
    expect(sel.dwellMs).toBe(200);
    sel.setDwellMs(99999);
    expect(sel.dwellMs).toBe(5000);
  });

  it('does not fire again until the pointer leaves and the refractory period has passed', () => {
    const sel = new DwellSelector(500, 12, 1500);
    sel.setTargets(RECTS);
    const first = still(sel, 150, 130, 0, 900).filter((e) => e.type === 'activate');
    const away = still(sel, 700, 400, 950, 1100);
    const backSoon = still(sel, 150, 130, 1150, 1900).filter((e) => e.type === 'activate'); // inside refractory
    const later = still(sel, 150, 130, 2000, 4200).filter((e) => e.type === 'activate');
    expect(first).toHaveLength(1);
    expect(away.some((e) => e.type === 'activate')).toBe(false);
    expect(backSoon).toHaveLength(0);
    expect(later).toHaveLength(1);
  });

  it('switching targets restarts the dwell, and removing the current target resets', () => {
    const sel = new DwellSelector(1000);
    sel.setTargets(RECTS);
    still(sel, 150, 130, 0, 600);
    const ev = still(sel, 350, 130, 700, 1500);
    expect(ev.some((e) => e.type === 'activate')).toBe(false); // only ~800 ms on the second target
    sel.setTargets([]);
    expect(sel.update({ x: 350, y: 130, t: 1600 })).toEqual([]);
    sel.reset();
  });
});

describe('calibration', () => {
  it('recovers scale and offset from three or more points and applies them', () => {
    const truth = (p: { x: number; y: number }) => ({ x: 1.4 * p.x - 30, y: 0.8 * p.y + 55 });
    const targets = [
      { x: 100, y: 100 },
      { x: 700, y: 120 },
      { x: 400, y: 500 },
      { x: 60, y: 480 },
    ];
    // measured = inverse of the truth mapping, as a tracker with drift would report
    const pairs = targets.map((t) => ({ target: t, measured: { x: (t.x + 30) / 1.4, y: (t.y - 55) / 0.8 } }));
    const c = fitCalibration(pairs);
    for (const p of pairs) {
      const out = applyCalibration(c, p.measured);
      expect(out.x).toBeCloseTo(p.target.x, 3);
      expect(out.y).toBeCloseTo(p.target.y, 3);
    }
    void truth;
  });

  it('rejects too few or degenerate points', () => {
    expect(() => fitCalibration([{ measured: { x: 0, y: 0 }, target: { x: 0, y: 0 } }])).toThrow(/three/);
    const same = { measured: { x: 5, y: 5 }, target: { x: 10, y: 10 } };
    expect(() => fitCalibration([same, same, same])).toThrow(/differ/);
  });
});

describe('switch scanning driver', () => {
  it('advances focus by itself, selects on one press, goes back on a long press, and supports pause', () => {
    const sch = new ManualScheduler();
    const drv = new SwitchScanDriver(sch, 1000);
    const got: IntentEvent[] = [];
    drv.start((e) => got.push(e));
    sch.advance(3500);
    expect(got.map((e) => e.intent)).toEqual(['next', 'next', 'next']);
    drv.press();
    drv.pressLong();
    expect(got.slice(3).map((e) => e.intent)).toEqual(['activate', 'back']);
    expect(got.every((e) => e.device === 'switch-scan')).toBe(true);

    drv.pause(true);
    sch.advance(5000);
    drv.press();
    expect(got).toHaveLength(5); // nothing while paused
    drv.pause(false);
    sch.advance(1000);
    expect(got).toHaveLength(6);

    drv.setIntervalMs(100); // clamped to 300
    expect(drv.intervalMs).toBe(300);
    sch.advance(900);
    expect(got.length).toBe(9);
    drv.stop();
    sch.advance(5000);
    expect(got.length).toBe(9);
  });
});

describe('dwell gaze driver', () => {
  it('turns gaze samples into select and activate intents, and pausing stops everything', () => {
    const drv = new DwellGazeDriver({ dwellMs: 600 });
    drv.setTargets(RECTS);
    const got: IntentEvent[] = [];
    drv.start((e) => got.push(e));
    for (let t = 0; t <= 900; t += 50) drv.feed({ x: 350, y: 130, t });
    expect(got.map((e) => [e.intent, e.targetId])).toEqual([
      ['select', 'deaf'],
      ['activate', 'deaf'],
    ]);
    drv.pause(true);
    for (let t = 2000; t <= 3000; t += 50) drv.feed({ x: 150, y: 130, t });
    expect(got).toHaveLength(2);
    drv.pause(false);
    expect(drv.paused).toBe(false);
  });

  it('applies calibration before dwelling', () => {
    const drv = new DwellGazeDriver({ dwellMs: 300 });
    drv.setTargets(RECTS);
    drv.setDwellMs(300);
    drv.setCalibration({ gainX: 1, offsetX: 100, gainY: 1, offsetY: 0 }); // tracker reads 100 px too far left
    const got: IntentEvent[] = [];
    drv.start((e) => got.push(e));
    const dwell: string[] = [];
    drv.onDwell = (e) => dwell.push(e.type);
    for (let t = 0; t <= 500; t += 50) drv.feed({ x: 50, y: 130, t }); // 50 + 100 = 150: inside "ack"
    expect(got.find((e) => e.intent === 'activate')?.targetId).toBe('ack');
    expect(dwell).toContain('progress');
  });
});

describe('keyboard driver', () => {
  function fakeTarget() {
    let handler: ((e: KeyEventLike) => void) | undefined;
    return {
      target: {
        addEventListener: (_t: 'keydown', fn: (e: KeyEventLike) => void) => void (handler = fn),
        removeEventListener: () => void (handler = undefined),
      },
      press: (key: string) => {
        const e = { key, preventDefault: vi.fn(), stopPropagation: vi.fn() };
        handler?.(e);
        return e;
      },
      attached: () => handler !== undefined,
    };
  }

  it('maps keys to intents, intercepts them, ignores others, and honours pause', () => {
    const k = fakeTarget();
    const drv = new KeyboardDriver(k.target, new ManualScheduler());
    const got: IntentEvent[] = [];
    drv.start((e) => got.push(e));
    expect(k.attached()).toBe(true);
    for (const key of ['ArrowRight', 'ArrowLeft', 'Enter', 'a']) k.press(key);
    expect(got.map((e) => e.intent)).toEqual(['next', 'back', 'activate', 'acknowledge']);
    const handled = k.press(' ');
    expect(handled.preventDefault).toHaveBeenCalled();
    expect(handled.stopPropagation).toHaveBeenCalled();
    const unhandled = k.press('x');
    expect(unhandled.preventDefault).not.toHaveBeenCalled();
    drv.pause(true);
    k.press('Enter');
    expect(got).toHaveLength(5);
    drv.stop();
    expect(k.attached()).toBe(false);
    expect(Object.keys(KEY_INTENTS).length).toBeGreaterThan(5);
  });
});

describe('scripted driver', () => {
  it('plays a fixed sequence on the scheduler and can be stopped', () => {
    const sch = new ManualScheduler();
    const drv = new ScriptedDriver(
      [
        { afterMs: 500, intent: 'next' },
        { afterMs: 500, intent: 'activate' },
        { afterMs: 1000, intent: 'select', targetId: 'deaf' },
      ],
      sch,
    );
    const got: IntentEvent[] = [];
    drv.start((e) => got.push(e));
    sch.advance(1200);
    expect(got.map((e) => [e.intent, e.at])).toEqual([
      ['next', 500],
      ['activate', 1000],
    ]);
    sch.advance(1000);
    expect(got[2]).toMatchObject({ intent: 'select', targetId: 'deaf', device: 'scripted' });
    const again = new ScriptedDriver([{ afterMs: 100, intent: 'next' }], sch);
    const seen: IntentEvent[] = [];
    again.start((e) => seen.push(e));
    again.stop();
    sch.advance(500);
    expect(seen).toEqual([]);
  });
});

describe('webcam head tracking seam', () => {
  it('is honestly unavailable without a landmark model, and says why; the controller never starts it', () => {
    const drv = new WebcamHeadDriver();
    expect(drv.available).toBe(false);
    expect(drv.unavailableReason).toMatch(/MediaPipe Tasks model files are not vendored/);
    const c = new IntentController();
    const start = vi.spyOn(drv, 'start');
    c.attach(drv);
    expect(start).not.toHaveBeenCalled();
    expect(new WebcamHeadDriver({ ready: true }).available).toBe(true);
  });
});

describe('intent controller', () => {
  function targets(log: string[] = []): Target[] {
    const mk = (id: string, kind: Target['kind'] = 'action'): Target => ({
      id,
      label: id,
      kind,
      activate: (ctx: ActivationContext) => void log.push(`${id}:${ctx.via}`),
    });
    return [mk('arrive'), mk('ack', 'acknowledge'), mk('persona-deaf', 'persona'), mk('persona-blind', 'persona')];
  }

  it('moves focus with next/back (wrapping) and activates the focused target, recording how', () => {
    const log: string[] = [];
    const focus: (string | undefined)[] = [];
    const c = new IntentController({ onFocus: (t) => focus.push(t?.id) });
    c.setTargets(targets(log));
    const ev = (intent: IntentEvent['intent'], extra: Partial<IntentEvent> = {}): IntentEvent => ({
      intent,
      device: 'switch-scan',
      at: 0,
      ...extra,
    });
    expect(c.handle(ev('activate'))).toBe(false); // nothing focused yet
    c.handle(ev('next'));
    c.handle(ev('next'));
    expect(c.focused?.id).toBe('ack');
    c.handle(ev('back'));
    c.handle(ev('back'));
    expect(c.focused?.id).toBe('persona-blind'); // wrapped
    c.handle(ev('next'));
    expect(c.handle(ev('activate'))).toBe(true);
    expect(log).toEqual(['arrive:intent:switch-scan']);
    expect(c.log[0]).toMatchObject({ targetId: 'arrive', device: 'switch-scan', via: 'intent:switch-scan', intent: 'activate' });
    expect(focus.length).toBeGreaterThan(3);
  });

  it('"acknowledge" jumps straight to the acknowledge target; "select" focuses by id; unknown ids are ignored', () => {
    const log: string[] = [];
    const c = new IntentController();
    c.setTargets(targets(log));
    expect(c.handle({ intent: 'acknowledge', device: 'keyboard', at: 0 })).toBe(true);
    expect(log).toEqual(['ack:intent:keyboard']);
    c.handle({ intent: 'select', device: 'dwell-gaze', at: 0, targetId: 'persona-deaf' });
    expect(c.focused?.id).toBe('persona-deaf');
    expect(c.handle({ intent: 'activate', device: 'dwell-gaze', at: 0, targetId: 'ghost' })).toBe(false);
    const none = new IntentController();
    none.setTargets([{ id: 'x', label: 'x', kind: 'action', activate: () => undefined }]);
    expect(none.handle({ intent: 'acknowledge', device: 'keyboard', at: 0 })).toBe(false);
  });

  it('false-activation guard: an item focused a moment ago is not activated', () => {
    let now = 0;
    const log: string[] = [];
    const c = new IntentController({ minFocusMs: 300, now: () => now });
    c.setTargets(targets(log));
    c.handle({ intent: 'next', device: 'switch-scan', at: 0 });
    now = 100;
    expect(c.handle({ intent: 'activate', device: 'switch-scan', at: 100 })).toBe(false);
    now = 400;
    expect(c.handle({ intent: 'activate', device: 'switch-scan', at: 400 })).toBe(true);
    expect(log).toHaveLength(1);
  });

  it('pause tracking is always available: it pauses every device and blocks intents until resumed', () => {
    const sch = new ManualScheduler();
    const log: string[] = [];
    const c = new IntentController({ now: () => sch.now() });
    c.setTargets(targets(log));
    const drv = new SwitchScanDriver(sch, 500);
    c.attach(drv);
    sch.advance(1000);
    expect(c.focused).toBeDefined();
    c.setPaused(true);
    expect(c.isPaused).toBe(true);
    expect(drv.paused).toBe(true);
    const before = c.focused?.id;
    sch.advance(5000);
    drv.press();
    expect(c.focused?.id).toBe(before);
    expect(log).toEqual([]);
    expect(c.handle({ intent: 'activate', device: 'x', at: 0 })).toBe(false);
    c.setPaused(false);
    sch.advance(500);
    expect(c.focused?.id).not.toBe(before);
    c.detachAll();
  });

  it('keeps the focused target when the target list is refreshed, and survives an empty list', () => {
    const c = new IntentController();
    c.setTargets(targets());
    c.handle({ intent: 'select', device: 'd', at: 0, targetId: 'ack' });
    c.setTargets([...targets()].reverse());
    expect(c.focused?.id).toBe('ack');
    c.setTargets([]);
    expect(c.handle({ intent: 'next', device: 'd', at: 0 })).toBe(false);
    expect(c.focused).toBeUndefined();
  });

  it('a real scheduler exists for the app', () => {
    expect(typeof realScheduler.now()).toBe('number');
    const cancel = realScheduler.setTimeout(() => undefined, 1);
    cancel();
    realScheduler.setInterval(() => undefined, 1000)();
  });
});

describe('end to end with no pointer at all', () => {
  it('one switch acknowledges an alert and switches persona; every action is an intent from the switch', () => {
    const sch = new ManualScheduler();
    const acted: string[] = [];
    const t = (id: string, kind: Target['kind']): Target => ({
      id,
      label: id,
      kind,
      activate: (ctx) => void acted.push(`${id}|${ctx.via}`),
    });
    const c = new IntentController({ now: () => sch.now(), minFocusMs: 200 });
    c.setTargets([t('ack', 'acknowledge'), t('persona-motor', 'persona'), t('persona-deaf', 'persona')]);
    const sw = new SwitchScanDriver(sch, 1000);
    c.attach(sw);
    sch.advance(1000); // focus: ack
    sch.advance(300);
    sw.press(); // acknowledge
    sch.advance(1000); // focus: persona-motor
    sch.advance(1000); // focus: persona-deaf
    sch.advance(300);
    sw.press(); // switch persona
    expect(acted).toEqual(['ack|intent:switch-scan', 'persona-deaf|intent:switch-scan']);
    expect(c.log.every((a) => a.via === 'intent:switch-scan' && a.device === 'switch-scan')).toBe(true);
  });
});
