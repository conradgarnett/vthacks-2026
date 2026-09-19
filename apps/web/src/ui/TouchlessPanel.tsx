import { useEffect, useRef, useState } from 'react';
import {
  DwellGazeDriver,
  IntentController,
  KeyboardDriver,
  SwitchScanDriver,
  WebcamHeadDriver,
  fitCalibration,
  type ActionRecord,
  type InputDevice,
  type KeyTarget,
  type Scheduler,
  type Target,
} from '@sense/touchless';
import type { Store } from '../store';
import { SimBadge } from './Badges';

export type DeviceId = 'off' | 'switch-scan' | 'dwell' | 'keyboard';

/** What tests and the demo use to drive the app through the SAME controller a user's device uses. */
export interface TouchlessHandle {
  controller: IntentController;
  /** Press the (single) switch. Only meaningful for one-switch scanning. */
  press(): void;
  setDevice(d: DeviceId): void;
  readonly device: DeviceId;
}

const DEVICE_LABEL: Record<DeviceId, string> = {
  off: 'Off (normal keyboard, touch and pointer)',
  'switch-scan': 'One-switch scanning (press Space or a switch)',
  dwell: 'Dwell selection (pointer stands in for a gaze, head or hand tracker)',
  keyboard: 'Keyboard intents (arrows, Enter, A to acknowledge)',
};

/** Everything marked data-touchless becomes something a device can reach. Focus is kept by stable id. */
function collectTargets(store: Store, elements: Map<string, HTMLElement>): Target[] {
  const seen = new Map<string, number>();
  const out: Target[] = [];
  elements.clear();
  for (const el of Array.from(document.querySelectorAll<HTMLElement>('[data-touchless]'))) {
    if ((el as HTMLButtonElement).disabled) continue;
    const label = el.getAttribute('data-touchless') ?? el.textContent ?? 'item';
    const n = (seen.get(label) ?? 0) + 1;
    seen.set(label, n);
    const id = n === 1 ? label : `${label} #${n}`;
    elements.set(id, el);
    const kind = (el.getAttribute('data-touchless-kind') as Target['kind'] | null) ?? 'action';
    out.push({
      id,
      label,
      kind,
      // `.click()` fires only a click event: no pointer or mouse events. The request it starts is audited as intent:<device>.
      activate: ({ via }) => void store.runVia(via, () => el.click()),
    });
  }
  return out;
}

const CAL_POINTS = [
  { x: 8, y: 8 },
  { x: 92, y: 8 },
  { x: 50, y: 50 },
  { x: 8, y: 80 },
  { x: 92, y: 80 },
];

export function TouchlessPanel({
  store,
  scheduler,
  initialDevice = 'off',
  onReady,
}: {
  store: Store;
  scheduler: Scheduler;
  initialDevice?: DeviceId;
  onReady?: (h: TouchlessHandle) => void;
}) {
  const [device, setDeviceState] = useState<DeviceId>(initialDevice);
  const [paused, setPaused] = useState(false);
  const [scanMs, setScanMs] = useState(1500);
  const [dwellMs, setDwellMs] = useState(1000);
  const [focusLabel, setFocusLabel] = useState('');
  const [lastAction, setLastAction] = useState<ActionRecord | undefined>();
  const [dwell, setDwell] = useState<{ id: string; fraction: number } | undefined>();
  const [calNote, setCalNote] = useState('');
  // Created once. The controller lives for the lifetime of the panel; its callbacks only touch stable state setters.
  const [elements] = useState(() => new Map<string, HTMLElement>());
  const scan = useRef<SwitchScanDriver | null>(null);
  const gaze = useRef<DwellGazeDriver | null>(null);
  const deviceRef = useRef<DeviceId>('off');
  const [webcam] = useState(() => new WebcamHeadDriver());

  const [controller] = useState(
    () =>
      new IntentController({
        now: () => scheduler.now(),
        minFocusMs: 250,
        refreshTargets: () => collectTargets(store, elements),
        onFocus: (t) => {
          for (const el of document.querySelectorAll('[data-intent-focus]')) el.removeAttribute('data-intent-focus');
          const el = t ? elements.get(t.id) : undefined;
          el?.setAttribute('data-intent-focus', 'true');
          el?.scrollIntoView?.({ block: 'center' });
          setFocusLabel(t ? `Focused: ${t.label}` : '');
        },
        onAction: (a) => setLastAction(a),
      }),
  );

  /** Swap the active input device (no React state here, so it is safe to call from effects). */
  function applyDevice(d: DeviceId): void {
    controller.detachAll();
    scan.current = null;
    gaze.current = null;
    deviceRef.current = d;
    let drv: InputDevice | undefined;
    if (d === 'switch-scan') {
      scan.current = new SwitchScanDriver(scheduler, scanMs);
      drv = scan.current;
    } else if (d === 'dwell') {
      gaze.current = new DwellGazeDriver({ dwellMs });
      gaze.current.onDwell = (e) => {
        if (e.type === 'progress') setDwell({ id: e.id, fraction: e.fraction });
        if (e.type === 'leave' || e.type === 'activate') setDwell(undefined);
      };
      drv = gaze.current;
    } else if (d === 'keyboard') {
      drv = new KeyboardDriver(document as unknown as KeyTarget, scheduler);
    }
    if (drv) controller.attach(drv);
    controller.setPaused(false);
    if (!drv) for (const el of document.querySelectorAll('[data-intent-focus]')) el.removeAttribute('data-intent-focus');
  }

  const setDevice = (d: DeviceId) => {
    applyDevice(d);
    setDeviceState(d);
    setPaused(false);
  };

  // Start with the requested device, and hand tests/demos a handle to the same controller.
  useEffect(() => {
    applyDevice(initialDevice);
    onReady?.({
      controller,
      press: () => scan.current?.press(),
      setDevice,
      get device() {
        return deviceRef.current;
      },
    });
    return () => controller.detachAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The single switch is the Space key while scanning; Escape always toggles pause, whatever the device.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && deviceRef.current !== 'off') {
        e.preventDefault();
        setPaused((p) => {
          controller.setPaused(!p);
          return !p;
        });
      } else if (deviceRef.current === 'switch-scan' && (e.key === ' ' || e.key === 'Enter')) {
        e.preventDefault();
        scan.current?.press();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [controller]);

  // Dwell mode: the pointer stands in for a tracker; targets' rectangles are refreshed as it moves.
  useEffect(() => {
    if (device !== 'dwell') return;
    let last = 0;
    const onMove = (e: MouseEvent) => {
      const now = scheduler.now();
      if (now - last > 200 || last === 0) {
        gaze.current?.setTargets(
          Array.from(collectTargets(store, elements), (t) => {
            const r = elements.get(t.id)?.getBoundingClientRect();
            return { id: t.id, x: r?.left ?? 0, y: r?.top ?? 0, w: r?.width ?? 0, h: r?.height ?? 0 };
          }),
        );
        last = now;
      }
      gaze.current?.feed({ x: e.clientX, y: e.clientY, t: now });
    };
    window.addEventListener('mousemove', onMove);
    return () => window.removeEventListener('mousemove', onMove);
  }, [device, scheduler, store, elements]);

  const calibrate = () => {
    // Example tracker drift (scale + offset). A real tracker would supply measured points here.
    const pairs = CAL_POINTS.map((t) => ({ target: t, measured: { x: (t.x + 6) / 1.08, y: (t.y - 4) / 0.95 } }));
    const c = fitCalibration(pairs);
    gaze.current?.setCalibration(c);
    setCalNote(
      `Calibrated from ${pairs.length} points: gain ${c.gainX.toFixed(2)} / ${c.gainY.toFixed(2)}, offset ${c.offsetX.toFixed(1)} / ${c.offsetY.toFixed(1)}. This used an example drift, not a real tracker.`,
    );
  };

  return (
    <section aria-labelledby="touch-h" className="panel">
      <h2 id="touch-h">
        Touchless: hands-free control <SimBadge label="NO CAMERA NEEDED" />
      </h2>
      <p className="meta">
        Everything in SENSE can be reached with one switch, a few keys, or dwell selection. Webcam head, eye or hand tracking is{' '}
        <strong>{webcam.available ? 'available' : 'not available in this build'}</strong>
        {!webcam.available && ` (${webcam.unavailableReason})`}.
      </p>
      <fieldset>
        <legend>Input device</legend>
        {(Object.keys(DEVICE_LABEL) as DeviceId[]).map((d) => (
          <div key={d}>
            <label>
              <input type="radio" name="touchless-device" checked={device === d} onChange={() => setDevice(d)} /> {DEVICE_LABEL[d]}
            </label>
          </div>
        ))}
      </fieldset>
      <div className="toolbar" role="group" aria-label="Touchless controls">
        <button
          type="button"
          aria-pressed={paused}
          disabled={device === 'off'}
          onClick={() => setPaused((p) => (controller.setPaused(!p), !p))}
        >
          {paused ? 'Resume tracking' : 'Pause tracking'} (Esc)
        </button>
        <button type="button" disabled={device !== 'switch-scan' || paused} onClick={() => scan.current?.press()}>
          Switch (or press Space)
        </button>
        <button type="button" data-touchless="Call the lift to floor 1" onClick={() => void store.post('/api/lift', { floor: 1 })}>
          Call the lift to floor 1
        </button>
      </div>
      <p>
        <label htmlFor="scan-ms">Scan speed: a new item every {(scanMs / 1000).toFixed(1)} s</label>{' '}
        <input
          id="scan-ms"
          type="range"
          min={500}
          max={4000}
          step={250}
          value={scanMs}
          onChange={(e) => {
            setScanMs(Number(e.target.value));
            scan.current?.setIntervalMs(Number(e.target.value));
          }}
        />
      </p>
      <p>
        <label htmlFor="dwell-ms">Dwell time: hold for {(dwellMs / 1000).toFixed(1)} s to select</label>{' '}
        <input
          id="dwell-ms"
          type="range"
          min={300}
          max={3000}
          step={100}
          value={dwellMs}
          onChange={(e) => {
            setDwellMs(Number(e.target.value));
            gaze.current?.setDwellMs(Number(e.target.value));
          }}
        />
      </p>
      {dwell && (
        <div
          className="dwell-ring"
          role="progressbar"
          aria-label={`Dwell progress on ${dwell.id}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(dwell.fraction * 100)}
        >
          <span style={{ width: `${Math.round(dwell.fraction * 100)}%` }} />
        </div>
      )}
      <p className="meta" role="status" aria-live="polite">
        {paused ? 'Tracking is paused. Nothing will be selected until you resume.' : focusLabel}
        {lastAction ? ` Last action: ${lastAction.label} via ${lastAction.via}.` : ''}
      </p>
      <details>
        <summary>Calibration</summary>
        <div className="calibration-pad" aria-hidden="true">
          {CAL_POINTS.map((p, i) => (
            <span key={i} className="cal-dot" style={{ left: `${p.x}%`, top: `${p.y}%` }}>
              {i + 1}
            </span>
          ))}
        </div>
        <p className="meta">
          Five calibration points. Tremor smoothing ignores small involuntary movements; calibration fits scale and offset to your tracker.
        </p>
        <button type="button" disabled={device !== 'dwell'} onClick={calibrate}>
          Calibrate with example tracker drift
        </button>
        <p role="status">{calNote}</p>
      </details>
    </section>
  );
}
