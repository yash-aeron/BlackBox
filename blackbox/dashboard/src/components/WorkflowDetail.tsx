import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { WorkflowDetail as WorkflowDetailModel } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { actionLabel, actionType, joinList, normalizeToken, truncate } from '../lib/util';
import { KeyValues, ResourceView } from './Panel';
import { ConfidenceBar } from './ConfidenceBar';
import { StatusBadge } from './StatusBadge';

export interface WorkflowDetailProps {
  targetId: string;
  workflowId: string | null;
}

/** One parameterized workflow: parameters, ordered steps, evidence. */
export function WorkflowDetail({ targetId, workflowId }: WorkflowDetailProps): ReactElement {
  const resource = useApiData<WorkflowDetailModel>(
    workflowId ? () => api.workflow(targetId, workflowId) : null,
    [targetId, workflowId],
  );

  return (
    <ResourceView
      resource={resource}
      loadingLabel="loading workflow…"
      emptyMessage="no workflow selected"
    >
      {(workflow) => (
        <>
          <p className="badge-row">
            <StatusBadge value={workflow.source ?? 'discovered'} kind="kind" />
            <StatusBadge value={`${workflow.verification_count ?? 0} verifications`} kind="kind" />
            <ConfidenceBar value={workflow.confidence} />
          </p>

          <KeyValues
            entries={[
              { label: 'id', value: workflow.workflow_id, mono: true },
              { label: 'name', value: workflow.name || '—' },
              { label: 'goal', value: workflow.goal || '—' },
              { label: 'expected end state', value: workflow.expected_end_state || '—', mono: true },
              { label: 'start state', value: workflow.start_state_id || '—', mono: true },
              { label: 'updated', value: workflow.updated_at ?? '—' },
            ]}
          />

          <h3 className="side-sub">parameters ({workflow.parameters?.length ?? 0})</h3>
          {workflow.parameters && workflow.parameters.length > 0 ? (
            <table className="tbl">
              <thead>
                <tr>
                  <th scope="col">name</th>
                  <th scope="col">label</th>
                  <th scope="col">kind</th>
                  <th scope="col">required</th>
                  <th scope="col">example</th>
                  <th scope="col">target element</th>
                </tr>
              </thead>
              <tbody>
                {workflow.parameters.map((parameter) => (
                  <tr key={parameter.name}>
                    <td className="mono">{parameter.name}</td>
                    <td>{parameter.label || '—'}</td>
                    <td className="mono">{parameter.kind ?? '—'}</td>
                    <td>{parameter.required ? 'yes' : 'no'}</td>
                    <td className="mono">{parameter.example_value || '—'}</td>
                    <td className="mono">{truncate(parameter.target_element_id ?? '—', 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">this workflow takes no parameters</p>
          )}

          <h3 className="side-sub">steps ({workflow.steps?.length ?? 0})</h3>
          {workflow.steps && workflow.steps.length > 0 ? (
            <table className="tbl">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">description</th>
                  <th scope="col">action</th>
                  <th scope="col">type</th>
                  <th scope="col">risk</th>
                  <th scope="col">parameters</th>
                  <th scope="col">transition</th>
                </tr>
              </thead>
              <tbody>
                {workflow.steps.map((step, index) => (
                  <tr key={`${step.index}-${index}`}>
                    <td className="mono">{step.index}</td>
                    <td>{step.description || '—'}</td>
                    <td className="mono" title={actionLabel(step.action)}>
                      {truncate(actionLabel(step.action), 34)}
                    </td>
                    <td className="mono">{actionType(step.action) ?? '—'}</td>
                    <td>
                      <StatusBadge value={normalizeToken(step.action?.risk ?? 'LOW')} kind="risk" />
                    </td>
                    <td className="mono">{joinList(step.parameter_names) || '—'}</td>
                    <td className="mono">{truncate(step.transition_id ?? '—', 14)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">no steps recorded</p>
          )}

          <h3 className="side-sub">preconditions ({workflow.preconditions?.length ?? 0})</h3>
          {workflow.preconditions && workflow.preconditions.length > 0 ? (
            <ul className="effect-list">
              {workflow.preconditions.map((item, index) => (
                <li key={`${item}-${index}`} className="mono">
                  {item}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">none recorded</p>
          )}

          <h3 className="side-sub">evidence ({workflow.evidence_ids?.length ?? 0})</h3>
          <p className="muted mono">
            {workflow.evidence_ids && workflow.evidence_ids.length > 0
              ? workflow.evidence_ids.map((id) => truncate(id, 16)).join(', ')
              : 'no evidence ids'}
          </p>
        </>
      )}
    </ResourceView>
  );
}
