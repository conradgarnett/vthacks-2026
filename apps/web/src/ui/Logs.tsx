import type { DisclosureDto, SecurityEvent } from '@sense/protocol';
import { SECURITY_LABEL, timeLabel } from '../format';

export function DisclosureLog({ entries }: { entries: DisclosureDto[] }) {
  const leaked = entries.filter((e) => e.profileDataSent !== false).length;
  return (
    <section aria-labelledby="disc-h" className="panel">
      <h2 id="disc-h">Disclosure Log</h2>
      <p>
        <strong>
          Profile data sent to remote agents: {leaked === 0 ? 'none' : leaked} (of {entries.length} messages).
        </strong>{' '}
        Your sensory profile stays on this device. Each message below is exactly what was sent, to whom, with an ephemeral session id.
      </p>
      {entries.length === 0 ? (
        <p className="empty">Nothing sent yet.</p>
      ) : (
        // A scrollable region must be keyboard-focusable so keyboard users can scroll it (axe: scrollable-region-focusable).
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Disclosure log table">
          <table>
            <caption className="visually-hidden">Messages sent to remote agents</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">To</th>
                <th scope="col">Message</th>
                <th scope="col">Scope</th>
                <th scope="col">Exactly what was sent</th>
              </tr>
            </thead>
            <tbody>
              {[...entries]
                .reverse()
                .slice(0, 60)
                .map((e) => (
                  <tr key={e.id}>
                    <td>{timeLabel(e.timestamp)}</td>
                    <td>{e.to}</td>
                    <td>
                      {e.messageType}
                      {e.capability ? `: ${e.capability}` : ''}
                    </td>
                    <td>{e.scope?.join(', ') ?? '–'}</td>
                    <td>
                      <details>
                        <summary>Show JSON</summary>
                        <pre>{e.sent}</pre>
                      </details>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function SecurityLog({ events }: { events: SecurityEvent[] }) {
  return (
    <section aria-labelledby="sec-h" className="panel">
      <h2 id="sec-h">Security events</h2>
      <p className="meta">Rejected sources are never shown as information. They appear here only.</p>
      {events.length === 0 ? (
        <p className="empty">No security events.</p>
      ) : (
        <ul className="security-list" aria-label="Security events, newest first">
          {[...events]
            .reverse()
            .slice(0, 40)
            .map((e) => (
              <li key={e.id} data-kind={e.kind}>
                <strong>
                  <span aria-hidden="true">{e.kind === 'IDENTITY_REJECTED' || e.kind === 'SIGNATURE_INVALID' ? '✖' : '⚠'} </span>
                  {SECURITY_LABEL[e.kind] ?? e.kind}
                </strong>{' '}
                · {e.source} · {timeLabel(e.timestamp)}
                {e.failingStep ? ` · failed step ${e.failingStep}: ${e.failingStepName ?? ''}` : ''}
                <br />
                {e.message}
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}
