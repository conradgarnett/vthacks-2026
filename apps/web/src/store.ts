import { useSyncExternalStore } from 'react';
import { Presenter, type PresentResult } from '@sense/render';
import type { Percept, SensoryProfile, ServerEvent, StateSnapshot } from '@sense/protocol';
import type { ConnectionStatus, SenseApi } from './api';

/** What actually happened when a percept was presented (some devices lack voice or vibration). */
export interface Presented {
  speech?: PresentResult['speech'];
  audio?: PresentResult['audio'];
  haptic?: PresentResult['haptic'];
  /** The visual equivalent of a vibration is flashing; only used when reduced motion is off. */
  flash: boolean;
}

export interface UiState {
  snapshot: StateSnapshot | null;
  connection: ConnectionStatus;
  presented: Record<string, Presented>;
  muted: boolean;
  selectedAgent: string | undefined;
  error: string | undefined;
  /** Newest percept at urgency >= 3, for the assertive live region. */
  latestAlert: Percept | undefined;
  /** Newest percept below urgency 3, for the polite live region. */
  latestNotice: Percept | undefined;
}

const MAX_PERCEPTS = 300;

export interface Store {
  getState(): UiState;
  subscribe(fn: () => void): () => void;
  start(): () => void;
  refresh(): Promise<void>;
  post(path: string, body?: unknown, via?: string): Promise<unknown>;
  get(path: string): Promise<unknown>;
  select(fqdn: string | undefined): void;
  setMuted(muted: boolean): void;
  presenter: Presenter;
  api: SenseApi;
}

export function createStore(api: SenseApi, presenter: Presenter = new Presenter()): Store {
  let state: UiState = {
    snapshot: null,
    connection: 'connecting',
    presented: {},
    muted: false,
    selectedAgent: undefined,
    error: undefined,
    latestAlert: undefined,
    latestNotice: undefined,
  };
  const listeners = new Set<() => void>();
  const set = (patch: Partial<UiState>) => {
    state = { ...state, ...patch };
    for (const l of listeners) l();
  };
  const withSnapshot = (fn: (s: StateSnapshot) => StateSnapshot) => {
    if (state.snapshot) set({ snapshot: fn(state.snapshot) });
  };

  function present(p: Percept, profile: SensoryProfile) {
    const res: PresentResult = presenter.present(p, state.muted ? { ...profile, output: mutedOutput(profile) } : profile);
    const presented: Presented = {
      ...(res.speech ? { speech: res.speech } : {}),
      ...(res.audio ? { audio: res.audio } : {}),
      ...(res.haptic ? { haptic: res.haptic } : {}),
      flash: res.haptic === 'unsupported' && !profile.display.reducedMotion,
    };
    set({
      presented: { ...state.presented, [p.id]: presented },
      ...(p.urgency >= 3 ? { latestAlert: p } : { latestNotice: p }),
    });
  }

  function onEvent(e: ServerEvent) {
    switch (e.type) {
      case 'state':
        set({ snapshot: e.state, error: undefined });
        break;
      case 'percept':
        if (!state.snapshot || state.snapshot.percepts.some((p) => p.id === e.percept.id)) break;
        withSnapshot((s) => ({ ...s, percepts: [...s.percepts, e.percept].slice(-MAX_PERCEPTS) }));
        if (state.snapshot) present(e.percept, state.snapshot.profile);
        break;
      case 'security':
        withSnapshot((s) => ({ ...s, security: [...s.security, e.event].slice(-200) }));
        break;
      case 'verification':
        withSnapshot((s) => ({
          ...s,
          verifications: [...s.verifications.filter((v) => v.fqdn !== e.verification.fqdn), e.verification],
        }));
        break;
      case 'disclosure':
        withSnapshot((s) => ({ ...s, disclosure: [...s.disclosure, e.entry].slice(-500) }));
        break;
      case 'ack':
        withSnapshot((s) => ({ ...s, acknowledged: Array.from(new Set([...s.acknowledged, e.perceptId])) }));
        break;
      case 'action':
        withSnapshot((s) => ({ ...s, actions: [...s.actions, e.action].slice(-200) }));
        break;
      case 'profile':
        withSnapshot((s) => ({ ...s, profile: e.profile }));
        break;
      case 'scent':
        withSnapshot((s) => ({ ...s, scent: e.scent }));
        break;
    }
  }

  const store: Store = {
    getState: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    start() {
      void store.refresh();
      return api.connect(onEvent, (connection) => set({ connection }));
    },
    async refresh() {
      try {
        set({ snapshot: await api.getState(), error: undefined });
      } catch (err) {
        set({ error: err instanceof Error ? err.message : 'Could not reach the SENSE server.' });
      }
    },
    async post(path, body, via) {
      try {
        const res = await api.post(path, body, via);
        set({ error: undefined });
        return res;
      } catch (err) {
        set({ error: err instanceof Error ? err.message : 'Request failed.' });
        return undefined;
      }
    },
    get: (path) => api.get(path),
    select: (selectedAgent) => set({ selectedAgent }),
    setMuted: (muted) => set({ muted }),
    presenter,
    api,
  };
  return store;
}

/** When the user mutes SENSE's voice and sounds, keep visual and haptic output only. */
function mutedOutput(profile: SensoryProfile): SensoryProfile['output'] {
  const out = structuredClone(profile.output);
  for (const k of Object.keys(out) as (keyof typeof out)[]) {
    const kept = (out[k] ?? []).filter((m) => m === 'visual' || m === 'haptic');
    out[k] = kept.length > 0 ? kept : ['visual'];
  }
  return out;
}

export function useStore(store: Store): UiState {
  return useSyncExternalStore(store.subscribe, store.getState, store.getState);
}
