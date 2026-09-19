import type { VerificationDto } from '@sense/protocol';
import { STEP_STATUS } from '../format';
import { TierBadge } from './Badges';

function outcomeTier(v: VerificationDto) {
  return v.outcome;
}

/** Step-by-step identity verification with evidence for every step. */
export function TrustInspector({
  verifications,
  selected,
  onSelect,
}: {
  verifications: VerificationDto[];
  selected: string | undefined;
  onSelect: (fqdn: string) => void;
}) {
  const current = verifications.find((v) => v.fqdn === selected) ?? verifications[0];
  const sorted = [...verifications].sort((a, b) => a.fqdn.localeCompare(b.fqdn));
  return (
    <section aria-labelledby="trust-h" className="panel">
      <h2 id="trust-h">Trust Inspector</h2>
      <p className="meta">Identity checks are ANS-modeled (a local simulation), not a real ANS registry.</p>
      {sorted.length === 0 ? (
        <p className="empty">No sources checked yet.</p>
      ) : (
        <>
          <div role="group" aria-label="Sources" className="toolbar">
            {sorted.map((v) => (
              <button
                key={v.fqdn}
                type="button"
                aria-pressed={v.fqdn === current?.fqdn}
                onClick={() => onSelect(v.fqdn)}
                title={`${v.displayName}: ${v.outcome}`}
              >
                <span aria-hidden="true">{v.outcome === 'VERIFIED' ? '✔' : v.outcome === 'REJECTED' ? '✖' : '⚠'} </span>
                {v.fqdn}
                <span className="visually-hidden"> {v.outcome}</span>
              </button>
            ))}
          </div>
          {current && <Detail v={current} />}
        </>
      )}
    </section>
  );
}

function Detail({ v }: { v: VerificationDto }) {
  const passed = v.steps.filter((s) => s.status === 'pass').length;
  return (
    <div aria-live="polite">
      <h3>
        {v.displayName} <span className="meta">({v.fqdn})</span>
      </h3>
      <p className="meta">
        <TierBadge tier={outcomeTier(v)} />
        <span>
          {passed} of {v.steps.length} steps passed
          {v.failingStep ? `. Rejected at step ${v.failingStep}: ${v.failingStepName}` : ''}
          {v.unavailableStep ? `. Step ${v.unavailableStep} could not be checked` : ''}
        </span>
      </p>
      <ol className="steps" aria-label={`Verification steps for ${v.fqdn}`}>
        {v.steps.map((s) => {
          const d = STEP_STATUS[s.status];
          return (
            <li key={s.step} className="step" data-status={s.status}>
              <span aria-hidden="true">{d.symbol}</span>
              <div>
                <strong>
                  Step {s.step}: {s.name}
                </strong>{' '}
                <span className="badge badge-neutral">
                  <span aria-hidden="true">{d.symbol}</span> {d.word}
                </span>
                <ul className="evidence">
                  {s.evidence.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="meta">
        Offers: {v.capabilities.join(', ') || 'nothing'}. Subscribed to: {v.subscriptions.join(', ') || 'nothing'}. Session{' '}
        {v.sessionId.slice(0, 8)}… (ephemeral).
      </p>
    </div>
  );
}
