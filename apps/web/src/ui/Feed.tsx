import { route } from '@sense/render';
import type { Percept, SensoryProfile } from '@sense/protocol';
import { directionLabel, timeLabel, urgencyLabel } from '../format';
import type { Presented } from '../store';
import { SimBadge, TierBadge } from './Badges';

interface FeedProps {
  percepts: Percept[];
  profile: SensoryProfile;
  acknowledged: string[];
  presented: Record<string, Presented>;
  onAction: (percept: Percept, actionId: string) => void;
}

/** Live regions: assertive for urgency >= 3 (role=alert), polite for everything else. */
export function LiveRegions({ alert, notice }: { alert: Percept | undefined; notice: Percept | undefined }) {
  return (
    <>
      <div id="alert-region" className="alert-banner" role="alert" aria-live="assertive" aria-atomic="true" hidden={!alert}>
        {alert && (
          <>
            <span aria-hidden="true">⚠ </span>
            {alert.short}
            {alert.spatial ? ` (${directionLabel(alert)})` : ''}
          </>
        )}
      </div>
      <div className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        {notice?.short}
      </div>
    </>
  );
}

const KIND_LABEL: Record<Percept['kind'], string> = {
  alert: 'Alert',
  description: 'Description',
  answer: 'Answer',
  status: 'Status',
  action: 'Action',
};

export function PerceptCard({
  p,
  profile,
  acked,
  presented,
  onAction,
}: {
  p: Percept;
  profile: SensoryProfile;
  acked: boolean;
  presented: Presented | undefined;
  onAction: FeedProps['onAction'];
}) {
  const plan = route(p, profile);
  const dir = directionLabel(p);
  const strong = p.urgency >= 3;
  return (
    <li
      className={`card${presented?.flash ? ' flash' : ''}`}
      data-urgency={p.urgency}
      data-tier={p.provenance.tier}
      aria-label={`${KIND_LABEL[p.kind]}: ${p.short}`}
    >
      <div className="meta">
        <TierBadge tier={p.provenance.tier} confidence={p.provenance.confidence} />
        <span>
          <strong>{p.provenance.sourceLabel ?? p.provenance.source}</strong>
          {p.provenance.sourceLabel ? ` (${p.provenance.source})` : ''}
        </span>
        {p.simulated && <SimBadge />}
        <span>{KIND_LABEL[p.kind]}</span>
        <span>{urgencyLabel(p.urgency)}</span>
        <time dateTime={p.timestamp}>{timeLabel(p.timestamp)}</time>
        {p.count && p.count > 1 && <span>×{p.count}</span>}
      </div>
      <p className="short">{p.short}</p>
      {dir && (
        <p className="meta">
          <span aria-hidden="true">🧭</span> Direction: {dir}
        </p>
      )}
      {plan.caption && (
        <p className="meta">
          <span aria-hidden="true">💬</span> Caption: {plan.caption}
          {presented?.speech === 'captions-only' && ' (voice not available on this device, captions only)'}
          {presented?.audio === 'unavailable' && ' (audio cues not available on this device)'}
        </p>
      )}
      {plan.haptic && (
        <div className="haptic-strip" role="note">
          <span aria-hidden="true">📳</span> Vibration: {plan.haptic.description}.{' '}
          {presented?.haptic === 'vibrated' ? 'Sent to your device.' : 'This device cannot vibrate, so the pattern is shown here.'}
        </div>
      )}
      <details>
        <summary>Details and evidence ({p.provenance.evidence.length})</summary>
        {p.long && <p>{p.long}</p>}
        <ul className="evidence">
          {p.provenance.evidence.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
        <p className="meta">
          Shown via: {plan.modalities.join(', ')}
          {strong ? '. Announced immediately (assertive).' : '.'}
        </p>
      </details>
      {p.actions && p.actions.length > 0 && (
        <div className="toolbar" role="group" aria-label="Actions for this item">
          {p.actions.map((a) => {
            const isAck = a.id === 'ack';
            return (
              <button
                key={a.id}
                type="button"
                className={isAck && !acked ? 'primary' : ''}
                data-touchless={isAck ? `Acknowledge: ${p.short}` : `${a.label}: ${p.short}`}
                data-touchless-kind={isAck ? 'acknowledge' : 'action'}
                disabled={isAck && acked}
                onClick={() => onAction(p, a.id)}
              >
                {isAck && acked ? 'Acknowledged ✔' : a.label}
              </button>
            );
          })}
        </div>
      )}
    </li>
  );
}

export function Feed({ percepts, profile, acknowledged, presented, onAction }: FeedProps) {
  const ordered = [...percepts].reverse();
  return (
    <section aria-labelledby="feed-h" className="panel">
      <h2 id="feed-h">Live percept feed</h2>
      <p className="meta">Newest first. Every item shows where it came from and how far to trust it.</p>
      {ordered.length === 0 ? (
        <p className="empty">Nothing yet. Choose “Arrive at Riverside Hall” in the simulated world controls.</p>
      ) : (
        <ol className="feed" aria-label="Percepts, newest first">
          {ordered.map((p) => (
            <PerceptCard
              key={p.id}
              p={p}
              profile={profile}
              acked={acknowledged.includes(p.id)}
              presented={presented[p.id]}
              onAction={onAction}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
