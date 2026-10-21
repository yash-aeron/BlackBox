import { useState } from 'react';
import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { WorkflowSummary } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { describeValue, toError, truncate } from '../lib/util';
import { EmptyState, ErrorState, Panel, ResourceView } from './Panel';
import { ConfidenceBar } from './ConfidenceBar';
import { StatusBadge } from './StatusBadge';
import { WorkflowDetail } from './WorkflowDetail';

export interface WorkflowListProps {
  targetId: string;
}

/** Mined workflows: list on the left, full detail (parameters + steps) on the right. */
export function WorkflowList({ targetId }: WorkflowListProps): ReactElement {
  const resource = useApiData<WorkflowSummary[]>(() => api.workflows(targetId), [targetId]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mining, setMining] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [mineError, setMineError] = useState<unknown>(null);

  const workflows = resource.data ?? [];
  const selected = workflows.find((workflow) => workflow.workflow_id === selectedId) ?? null;

  const mine = (): void => {
    setMining(true);
    setNotice(null);
    setMineError(null);
    api.mineWorkflows(targetId).then(
      (result) => {
        setMining(false);
        setNotice(`re-mined: ${describeValue(result) || 'done'}`);
        resource.reload();
      },
      (cause: unknown) => {
        setMining(false);
        setMineError(toError(cause));
      },
    );
  };

  return (
    <div className="split">
      <Panel
        title="Workflows"
        subtitle={`${workflows.length} mined`}
        actions={
          <>
            <button type="button" className="btn btn-small" onClick={mine} disabled={mining}>
              {mining ? 'mining…' : 're-mine'}
            </button>
            <button type="button" className="btn btn-small" onClick={resource.reload}>
              refresh
            </button>
          </>
        }
      >
        {notice ? <p className="notice-text">{notice}</p> : null}
        {mineError ? <ErrorState error={mineError} /> : null}
        <ResourceView
          resource={resource}
          isEmpty={(rows) => rows.length === 0}
          emptyMessage="no workflows learned yet — run an exploration"
          emptyHint="a workflow is mined when a goal-shaped route has been verified more than once."
        >
          {(rows) => (
            <ul className="rows">
              {rows.map((workflow) => (
                <li key={workflow.workflow_id}>
                  <button
                    type="button"
                    className={`row-btn${workflow.workflow_id === selectedId ? ' row-active' : ''}`}
                    aria-pressed={workflow.workflow_id === selectedId}
                    onClick={() => setSelectedId(workflow.workflow_id)}
                  >
                    <span className="row-title">{workflow.name || workflow.goal || workflow.workflow_id}</span>
                    <span className="row-meta mono">
                      {truncate(workflow.workflow_id, 18)} · {workflow.steps?.length ?? 0} steps ·{' '}
                      {workflow.verification_count ?? 0} verifications
                    </span>
                    <span className="row-badges">
                      <StatusBadge value={workflow.source ?? 'discovered'} kind="kind" />
                      <ConfidenceBar value={workflow.confidence} label="conf" />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </ResourceView>
      </Panel>

      <Panel title="Workflow detail" subtitle={selected ? selected.workflow_id : 'nothing selected'}>
        {selectedId ? (
          <WorkflowDetail targetId={targetId} workflowId={selectedId} />
        ) : (
          <EmptyState
            message="select a workflow"
            hint="the detail shows its parameters, the ordered steps and the evidence behind it."
          />
        )}
      </Panel>
    </div>
  );
}
