import type { ReactElement } from 'react';
import type { TaskPlan } from '../api/types';
import { classNames, joinList, planSourceInfo, truncate } from '../lib/util';
import { ConfidenceBar } from './ConfidenceBar';
import { StatusBadge } from './StatusBadge';

export interface PlanViewProps {
  plan: TaskPlan;
  /** `result.plan_source`; falls back to `plan.source`. */
  planSource?: string | null;
  /** Workflow id that produced the plan, when one did. */
  usedWorkflow?: string | null;
  notes?: string[] | null;
}

/**
 * The plan the task ran against, with a badge that says where it came from:
 * workflow reuse, a graph route, exploration, or nothing.
 */
export function PlanView({ plan, planSource, usedWorkflow, notes }: PlanViewProps): ReactElement {
  const source = planSourceInfo(planSource ?? plan.source, usedWorkflow ?? plan.workflow_id);
  const allNotes = [...(plan.notes ?? []), ...(notes ?? [])];
  const steps = plan.steps ?? [];

  return (
    <section className="plan" aria-label="task plan">
      <header className="plan-head">
        <span className={classNames('plan-source', `plan-source-${source.kind}`)} title={`plan source: ${plan.source}`}>
          {source.label}
        </span>
        <span className="mono muted" title={plan.plan_id}>
          {truncate(plan.plan_id, 18)}
        </span>
        {plan.workflow_id ? <span className="mono muted">workflow {truncate(plan.workflow_id, 14)}</span> : null}
        <ConfidenceBar value={plan.confidence} label="plan" />
        <span className="mono muted">cost {plan.estimated_cost ?? '—'}</span>
      </header>

      <dl className="kv kv-inline">
        <div className="kv-row">
          <dt>start state</dt>
          <dd className="mono">{plan.start_state_id || '—'}</dd>
        </div>
        <div className="kv-row">
          <dt>expected end state</dt>
          <dd className="mono">{plan.expected_end_state || '—'}</dd>
        </div>
        <div className="kv-row">
          <dt>steps</dt>
          <dd className="mono">{steps.length}</dd>
        </div>
      </dl>

      {steps.length === 0 ? (
        <p className="state state-empty">the plan has no steps — the model knows no route for this goal</p>
      ) : (
        <table className="tbl tbl-plan">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">description</th>
              <th scope="col">action</th>
              <th scope="col">type</th>
              <th scope="col">risk</th>
              <th scope="col">target state</th>
              <th scope="col">parameters</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((step, index) => (
              <tr key={`${step.index}-${index}`}>
                <td className="mono">{step.index}</td>
                <td>{step.description || '—'}</td>
                <td className="mono" title={step.action}>
                  {truncate(step.action, 40)}
                </td>
                <td className="mono">{step.action_type}</td>
                <td>
                  <StatusBadge value={step.risk} kind="risk" />
                </td>
                <td className="mono">{truncate(step.target_state ?? '—', 14)}</td>
                <td className="mono" title={joinList(Object.entries(step.parameters ?? {}).map(([key, value]) => `${key}=${String(value)}`))}>
                  {truncate(joinList(Object.entries(step.parameters ?? {}).map(([key, value]) => `${key}=${String(value)}`)), 32) ||
                    '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {allNotes.length > 0 ? (
        <ul className="effect-list plan-notes">
          {allNotes.map((note, index) => (
            <li key={`${note}-${index}`}>{note}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
