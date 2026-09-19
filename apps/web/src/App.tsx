import { useEffect, useState } from 'react';
import type { Percept, SensoryProfile } from '@sense/protocol';
import { createStore, useStore, type Store } from './store';
import { createHttpApi } from './api';
import { ModeBadges } from './ui/Badges';
import { Feed, LiveRegions } from './ui/Feed';
import { TrustInspector } from './ui/TrustInspector';
import { DisclosureLog, SecurityLog } from './ui/Logs';
import { PersonaSwitcher, SceneControls } from './ui/Controls';
import { VisionPanel } from './ui/VisionPanel';
import { HearingPanel } from './ui/HearingPanel';

/** Apply the profile's display preferences (contrast, text size, motion) to the page. */
function useDisplayPrefs(profile: SensoryProfile | undefined): void {
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--text-scale', String(profile?.display.textScale ?? 1));
    if (profile?.display.highContrast) root.setAttribute('data-contrast', 'high');
    else root.removeAttribute('data-contrast');
    root.setAttribute('data-reduced-motion', String(profile?.display.reducedMotion ?? false));
    root.lang = profile?.language ?? 'en';
  }, [profile?.display.textScale, profile?.display.highContrast, profile?.display.reducedMotion, profile?.language]);
}

const TEST_PERCEPT: Percept = {
  id: 'test-sound',
  timestamp: new Date(0).toISOString(),
  sense: 'hearing',
  kind: 'status',
  urgency: 2,
  short: 'Sound and voice test.',
  provenance: { tier: 'INFERRED', source: 'device-test', confidence: 1, evidence: ['local test'] },
  spatial: { bearingDeg: 90, distanceM: 2 },
};

export function App({ store }: { store: Store }) {
  const ui = useStore(store);
  const snap = ui.snapshot;
  useDisplayPrefs(snap?.profile);
  useEffect(() => store.start(), [store]);
  const [showTestNote, setShowTestNote] = useState(false);

  const post = (path: string, body?: unknown) => store.post(path, body);

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="app-header">
        <div className="row">
          <h1>SENSE</h1>
          <span className="meta">Alt text for physical reality</span>
          {snap && <ModeBadges mode={snap.mode} />}
        </div>
        <div className="toolbar" role="group" aria-label="Sound and voice">
          <button type="button" aria-pressed={ui.muted} onClick={() => store.setMuted(!ui.muted)}>
            {ui.muted ? 'Voice and sounds are muted' : 'Mute voice and sounds'}
          </button>
          <button
            type="button"
            onClick={() => {
              if (snap) store.presenter.present(TEST_PERCEPT, snap.profile);
              setShowTestNote(true);
            }}
          >
            Test sound and voice
          </button>
          <span className="meta" role="status">
            {ui.connection === 'open'
              ? 'Connected to your local SENSE server.'
              : ui.connection === 'connecting'
                ? 'Connecting…'
                : 'Disconnected. Reconnecting…'}
            {showTestNote ? ' Test played (browsers may need one click before allowing sound).' : ''}
          </span>
        </div>
      </header>

      <main id="main">
        {ui.error && (
          <div className="notice" role="alert" style={{ gridColumn: '1 / -1' }}>
            <strong>Problem:</strong> {ui.error}
          </div>
        )}
        {!snap ? (
          <p role="status">Loading…</p>
        ) : (
          <>
            <div className="stack">
              <LiveRegions alert={ui.latestAlert} notice={ui.latestNotice} />
              <Feed
                percepts={snap.percepts}
                profile={snap.profile}
                acknowledged={snap.acknowledged}
                presented={ui.presented}
                onAction={(p, actionId) => {
                  if (actionId === 'ack') void post('/api/ack', { perceptId: p.id });
                }}
              />
              <SecurityLog events={snap.security} />
              <DisclosureLog entries={snap.disclosure} />
            </div>
            <div className="stack">
              <PersonaSwitcher profile={snap.profile} post={post} />
              <SceneControls post={post} />
              <VisionPanel post={post} aiLabel={snap.mode.ai} />
              <HearingPanel post={post} />
              <TrustInspector verifications={snap.verifications} selected={ui.selectedAgent} onSelect={store.select} />
            </div>
          </>
        )}
      </main>

      <footer>
        <p>
          <strong>Limitations.</strong> SENSE is a hackathon prototype. It is not a medical device and not a certified safety system. Do not
          rely on it for life-safety decisions. Everything shown by default comes from a simulated world. Identity checks are modeled on ANS
          and are not ANS-compliant. The interface targets WCAG 2.2 AA but is not certified.
        </p>
      </footer>
    </>
  );
}

export function createDefaultStore(): Store {
  return createStore(createHttpApi());
}
