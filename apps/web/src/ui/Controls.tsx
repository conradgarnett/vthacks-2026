import { useState } from 'react';
import { MODALITIES, PERSONAS, PERSONA_IDS, SITUATIONAL_PRESETS, type Modality, type SensoryProfile } from '@sense/protocol';
import { SimBadge } from './Badges';

interface PostFn {
  (path: string, body?: unknown): Promise<unknown>;
}

const PERSONA_HELP: Record<string, string> = {
  blind: 'Speech and spatial audio first.',
  deaf: 'Visual cards and vibration, never audio-only.',
  motor: 'Hands-free input; big targets and dwell.',
  anosmia: 'Smell-risk alerts as vision, vibration and voice.',
  ageusia: 'Taste, texture and ingredient descriptions.',
};

export function PersonaSwitcher({ profile, post }: { profile: SensoryProfile; post: PostFn }) {
  const [allergenText, setAllergenText] = useState<string>(profile.allergens.join(', '));
  return (
    <section aria-labelledby="persona-h" className="panel">
      <h2 id="persona-h">Sensory profile</h2>
      <p className="meta">Stays on this device. It is never sent to a remote agent.</p>
      <fieldset>
        <legend>Persona</legend>
        {PERSONA_IDS.map((id) => (
          <div key={id}>
            <label>
              <input
                type="radio"
                name="persona"
                id={`persona-${id}`}
                value={id}
                checked={profile.personaId === id}
                onChange={() => void post('/api/profile/persona', { personaId: id })}
              />{' '}
              <strong>{PERSONAS[id].name}</strong> <span className="meta">{PERSONA_HELP[id]}</span>
            </label>
          </div>
        ))}
      </fieldset>
      <fieldset>
        <legend>Right now (temporary)</legend>
        <label>
          <input
            type="radio"
            name="situation"
            checked={!profile.situational}
            onChange={() => void post('/api/profile/situation', { preset: null })}
          />{' '}
          Normal
        </label>{' '}
        {SITUATIONAL_PRESETS.map((preset) => (
          <label key={preset}>
            <input
              type="radio"
              name="situation"
              checked={profile.situational?.preset === preset}
              onChange={() => void post('/api/profile/situation', { preset })}
            />{' '}
            {preset === 'hands-full' ? 'Hands full' : 'Noisy room'}{' '}
          </label>
        ))}
      </fieldset>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void post('/api/profile/allergens', {
            allergens: allergenText
              .split(',')
              .map((a) => a.trim())
              .filter(Boolean),
          });
        }}
      >
        <label htmlFor="allergens">Declared allergens (comma separated, kept on this device)</label>
        <div className="toolbar">
          <input
            id="allergens"
            type="text"
            value={allergenText}
            onChange={(e) => setAllergenText(e.target.value)}
            placeholder="peanut, sesame"
          />
          <button type="submit">Save allergens</button>
        </div>
      </form>
      <CustomBuilder profile={profile} post={post} />
    </section>
  );
}

function CustomBuilder({ profile, post }: { profile: SensoryProfile; post: PostFn }) {
  const [name, setName] = useState('My profile');
  const [rate, setRate] = useState(profile.speechRate);
  const [verbosity, setVerbosity] = useState<SensoryProfile['verbosity']>(profile.verbosity);
  const [scale, setScale] = useState(profile.display.textScale);
  const [contrast, setContrast] = useState(profile.display.highContrast);
  const [output, setOutput] = useState<SensoryProfile['output']>(profile.output);

  const toggle = (u: string, m: Modality) => {
    const key = u as keyof SensoryProfile['output'];
    const cur = output[key] ?? [];
    setOutput({ ...output, [key]: cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m] });
  };

  return (
    <details>
      <summary>Custom profile builder</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void post('/api/profile/custom', {
            profile: {
              ...profile,
              id: `custom-${
                name
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, '-')
                  .slice(0, 40) || 'profile'
              }`,
              name,
              personaId: undefined,
              speechRate: rate,
              verbosity,
              output,
              display: { ...profile.display, textScale: scale, highContrast: contrast },
            },
          });
        }}
      >
        <p>
          <label htmlFor="cp-name">Name</label> <input id="cp-name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </p>
        <p>
          <label htmlFor="cp-rate">Speech rate: {rate.toFixed(1)}×</label>{' '}
          <input id="cp-rate" type="range" min={0.5} max={3} step={0.1} value={rate} onChange={(e) => setRate(Number(e.target.value))} />
        </p>
        <p>
          <label htmlFor="cp-verb">Verbosity</label>{' '}
          <select id="cp-verb" value={verbosity} onChange={(e) => setVerbosity(e.target.value as SensoryProfile['verbosity'])}>
            <option value="terse">Terse</option>
            <option value="normal">Normal</option>
            <option value="detailed">Detailed</option>
          </select>
        </p>
        <p>
          <label htmlFor="cp-scale">Text size: {Math.round(scale * 100)}%</label>{' '}
          <input id="cp-scale" type="range" min={1} max={2} step={0.25} value={scale} onChange={(e) => setScale(Number(e.target.value))} />
        </p>
        <p>
          <label>
            <input type="checkbox" checked={contrast} onChange={(e) => setContrast(e.target.checked)} /> High contrast
          </label>
        </p>
        {(['0', '1', '2', '3', '4'] as const).map((u) => (
          <fieldset key={u}>
            <legend>How to deliver urgency {u}</legend>
            {MODALITIES.map((m) => (
              <label key={m}>
                <input type="checkbox" checked={(output[u] ?? []).includes(m)} onChange={() => toggle(u, m)} /> {m}{' '}
              </label>
            ))}
          </fieldset>
        ))}
        <button type="submit" className="primary">
          Use this profile
        </button>
      </form>
    </details>
  );
}

const SCENE_BUTTONS: { label: string; event?: string; script?: string; arrive?: string; arg?: number }[] = [
  { label: 'Arrive at Riverside Hall', arrive: 'riverside' },
  { label: 'Fire alarm', event: 'fire-alarm' },
  { label: 'Spoofed “false alarm”', event: 'spoof-false-alarm' },
  { label: 'Clear alarm (source report)', event: 'clear-alarm' },
  { label: 'Smoke rising (script)', script: 'smoke' },
  { label: 'Alert flood (200 messages)', event: 'flood', arg: 200 },
  { label: 'Bring attackers online', event: 'activate-attackers' },
];

export function SceneControls({ post }: { post: PostFn }) {
  return (
    <section aria-labelledby="scene-h" className="panel">
      <h2 id="scene-h">
        Simulated world controls <SimBadge />
      </h2>
      <p className="meta">Demo controls. Every place, sensor and attacker here is simulated.</p>
      <div className="toolbar" role="group" aria-label="Simulated world events">
        {SCENE_BUTTONS.map((b) => (
          <button
            key={b.label}
            type="button"
            onClick={() => {
              if (b.arrive) void post('/api/arrive', { area: b.arrive });
              else if (b.script) void post('/api/world/play', { script: b.script });
              else if (b.event) void post('/api/world/event', { name: b.event, ...(b.arg !== undefined ? { arg: b.arg } : {}) });
            }}
          >
            {b.label}
          </button>
        ))}
      </div>
    </section>
  );
}
