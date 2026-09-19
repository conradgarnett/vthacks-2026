import type { StateSnapshot, Tier } from '@sense/protocol';
import { TIER_DISPLAY } from '../format';

export function TierBadge({ tier, confidence }: { tier: Tier; confidence?: number | undefined }) {
  const d = TIER_DISPLAY[tier];
  return (
    <span className={`badge badge-${tier.toLowerCase()}`} title={d.help}>
      <span aria-hidden="true">{d.symbol}</span>
      <span>
        {d.word}
        {confidence !== undefined ? ` ${Math.round(confidence * 100)}%` : ''}
      </span>
    </span>
  );
}

export function SimBadge({ label = 'SIMULATED' }: { label?: string }) {
  return (
    <span className="badge badge-sim" title="This information comes from the simulated world, not from a real place.">
      {label}
    </span>
  );
}

/** Persistent mode badges: SIMULATED WORLD is always shown; MOCK AI and ANS-modeled tell the truth about what is real. */
export function ModeBadges({ mode }: { mode: StateSnapshot['mode'] }) {
  return (
    <ul className="toolbar" aria-label="What is real and what is simulated" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      <li>
        <SimBadge label={mode.world} />
      </li>
      <li>
        <span className="badge badge-neutral" title="Identity checks are modeled on the Agent Name Service. This is not ANS compliance.">
          {mode.ans}
        </span>
      </li>
      <li>
        <span
          className="badge badge-neutral"
          title={
            mode.ai === 'MOCK AI' ? 'Recorded fixtures stand in for a language model.' : 'Vision and taste reasoning use the Anthropic API.'
          }
        >
          {mode.ai}
        </span>
      </li>
      <li>
        <span className="badge badge-neutral">{mode.online ? 'Online' : 'Offline mode'}</span>
      </li>
    </ul>
  );
}
