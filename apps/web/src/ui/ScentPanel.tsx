import type { ScentStatusDto } from '@sense/protocol';
import { TierBadge } from './Badges';

const LEVELS = ['No elevated reading reported', 'Elevated', 'High', 'Severe', 'Critical'];

/** ScentGuard: smoke risk from verified sensors, for people who cannot smell. Never an all-clear. */
export function ScentPanel({ status }: { status: ScentStatusDto | undefined }) {
  return (
    <section aria-labelledby="scent-h" className="panel">
      <h2 id="scent-h">ScentGuard: smoke risk</h2>
      <p className="meta">
        Levels come from transparent rules over verified sensors (thresholds are illustrative, not from a standard). Level 0 does not mean
        nothing is wrong.
      </p>
      {status ? (
        <div>
          <p>
            <strong>
              Level {status.level} of 4: {LEVELS[status.level]}
            </strong>{' '}
            <TierBadge tier={status.tier} />
          </p>
          <meter min={0} max={4} value={status.level} aria-label={`Smoke risk level ${status.level} of 4`}>
            {status.level} of 4
          </meter>
          <p role="status">{status.summary}</p>
          {status.rules.length > 0 && <p className="meta">Rules that fired: {status.rules.join(', ')}</p>}
        </div>
      ) : (
        <p className="empty">No air-quality data yet. Arrive somewhere with verified sensors, or use “Smoke rising (script)”.</p>
      )}
    </section>
  );
}
