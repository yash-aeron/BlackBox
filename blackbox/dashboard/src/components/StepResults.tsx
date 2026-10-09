import type { ReactElement } from 'react';
import type { StepResult, TaskVerification } from '../api/types';
import { classNames, describeValue, formatMs, truncate } from '../lib/util';
import { StatusBadge } from './StatusBadge';

export interface StepResultsProps {
  stepResults: StepResult[];
  verification?: TaskVerification | null;
}

function EffectColumn({
  title,
  items,
  tone,
  emptyMessage,
}: {
  title: string;
  items: string[];
  tone?: string;
  emptyMessage: string;
}): ReactElement {
  return (
    <div className={classNames('step-col', tone)}>
      <h5 className="step-col-head">{title}</h5>
      {items.length === 0 ? (
        <p className="muted">{emptyMessage}</p>
      ) : (
        <ul className="effect-list">
          {items.map((item, index) => (
            <li key={`${item}-${index}`} className="mono">
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Per-step verification plus the machine-checkable predicate outcomes. */
export function StepResults({ stepResults, verification }: StepResultsProps): ReactElement {
  const predicates = verification?.predicates ?? [];

  return (
    <section className="step-results" aria-label="step results">
      {stepResults.length === 0 ? (
        <p className="state state-empty">no steps were executed</p>
      ) : (
        <ol className="step-list">
          {stepResults.map((step, index) => (
            <li key={`${step.step_index}-${index}`} className={classNames('step-card', step.verified ? 'step-ok' : 'step-bad')}>
              <header className="step-head">
                <span className="step-check" aria-hidden="true">
                  {step.verified ? '✓' : '✗'}
                </span>
                <span className="step-index mono">step {step.step_index}</span>
                <span className="step-action mono" title={step.action}>
                  {truncate(step.action, 48)}
                </span>
                <StatusBadge value={step.status} />
                {step.no_effect ? <StatusBadge value="no effect" kind="kind" /> : null}
                <span className="muted mono">{formatMs(step.duration_ms)}</span>
                <span className="muted mono" title={step.locator ?? 'no locator recorded'}>
                  {step.locator ? truncate(step.locator, 34) : 'no locator'}
                </span>
              </header>

              <div className="step-cols">
                <EffectColumn title="expected" items={step.expected ?? []} emptyMessage="nothing predicted" />
                <EffectColumn title="matched" items={step.matched ?? []} tone="ok-text" emptyMessage="nothing matched" />
                <EffectColumn title="missed" items={step.missed ?? []} tone="err-text" emptyMessage="nothing missed" />
              </div>

              {step.error ? <p className="err-text mono">error: {step.error}</p> : null}
              {step.notes && step.notes.length > 0 ? (
                <ul className="effect-list">
                  {step.notes.map((note, noteIndex) => (
                    <li key={`${note}-${noteIndex}`}>{note}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      )}

      <h4 className="side-sub">predicate outcomes ({predicates.length})</h4>
      {predicates.length === 0 ? (
        <p className="muted">no predicates were supplied for this task</p>
      ) : (
        <table className="tbl tbl-predicates">
          <thead>
            <tr>
              <th scope="col">kind</th>
              <th scope="col">value</th>
              <th scope="col">satisfied</th>
              <th scope="col">evidence</th>
            </tr>
          </thead>
          <tbody>
            {predicates.map((predicate, index) => (
              <tr key={`${predicate.kind}-${index}`}>
                <td className="mono">{predicate.kind}</td>
                <td className="mono">{truncate(describeValue(predicate.value), 46) || '—'}</td>
                <td>
                  <StatusBadge value={predicate.satisfied ? 'SATISFIED' : 'NOT SATISFIED'} kind="kind" />
                </td>
                <td className="mono">{predicate.evidence ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {verification ? (
        <p className="muted mono">
          verification: {verification.steps_verified}/{verification.steps_total} steps ·{' '}
          {Math.round((verification.confidence ?? 0) * 100)}% confidence
          {verification.final_state_id ? ` · final state ${verification.final_state_id}` : ''}
        </p>
      ) : null}
    </section>
  );
}
