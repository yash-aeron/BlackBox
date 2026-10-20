import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { ApprovalDecision, ApprovalInbox } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { formatClock, joinList, toError, truncate } from '../lib/util';
import { EmptyState, ErrorState, KeyValues, Panel, ResourceView } from './Panel';
import { StatusBadge } from './StatusBadge';

export interface ApprovalQueueProps {
  targetId: string;
}

const POLL_INTERVAL_MS = 2000;

/**
 * Risky actions waiting for a human. While anything is pending the queue polls
 * every 2s so a decision made elsewhere (CLI, another browser) shows up here.
 */
export function ApprovalQueue({ targetId }: ApprovalQueueProps): ReactElement {
  const inbox = useApiData<ApprovalInbox>(() => api.approvals(targetId), [targetId]);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const pending = inbox.data?.pending ?? [];
  const pendingCount = pending.length;
  const reload = inbox.reload;

  useEffect(() => {
    if (pendingCount === 0) return undefined;
    const timer = window.setInterval(reload, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [pendingCount, reload]);

  const decide = (requestId: string, decision: ApprovalDecision): void => {
    setDecidingId(requestId);
    setDecisionError(null);
    setNotice(null);
    api.decide(targetId, requestId, decision).then(
      (result) => {
        setDecidingId(null);
        setNotice(`${decision.toLowerCase()} sent for ${requestId}${result?.status ? ` · ${result.status}` : ''}`);
        reload();
      },
      (cause: unknown) => {
        setDecidingId(null);
        setDecisionError(toError(cause));
      },
    );
  };

  const firstPending = pending[0]?.request_id ?? null;

  return (
    <Panel
      title="Approvals"
      subtitle={
        pendingCount > 0
          ? `${pendingCount} pending · polling every ${POLL_INTERVAL_MS / 1000}s`
          : 'nothing waiting for a decision'
      }
      actions={
        <>
          <button
            type="button"
            className="btn btn-small"
            disabled={!firstPending || decidingId !== null}
            onClick={() => {
              if (firstPending) decide(firstPending, 'PAUSE');
            }}
            title="pause the agent by pausing the oldest pending request"
          >
            pause agent
          </button>
          <button
            type="button"
            className="btn btn-small btn-danger"
            disabled={!firstPending || decidingId !== null}
            onClick={() => {
              if (firstPending) decide(firstPending, 'STOP');
            }}
            title="stop the agent by refusing the oldest pending request"
          >
            stop agent
          </button>
          <button type="button" className="btn btn-small" onClick={reload}>
            refresh
          </button>
        </>
      }
    >
      {notice ? <p className="notice-text">{notice}</p> : null}
      {decisionError ? <ErrorState error={decisionError} /> : null}

      <ResourceView
        resource={inbox}
        loadingLabel="loading approvals…"
        emptyMessage="no approval inbox returned"
      >
        {(data: ApprovalInbox) => (
          <>
            <div className="metric-grid metric-grid-compact">
              <div className="metric">
                <span className="metric-value mono">{data.pending?.length ?? 0}</span>
                <span className="metric-label">pending</span>
              </div>
              <div className="metric">
                <span className="metric-value mono">{data.decisions ?? 0}</span>
                <span className="metric-label">decisions</span>
              </div>
              <div className="metric">
                <span className="metric-value mono">{data.approved ?? 0}</span>
                <span className="metric-label">approved</span>
              </div>
              <div className="metric">
                <span className="metric-value mono">{data.rejected ?? 0}</span>
                <span className="metric-label">rejected</span>
              </div>
            </div>

            {(data.pending?.length ?? 0) === 0 ? (
              <EmptyState
                message="no requests waiting"
                hint="an approval appears here when the agent proposes an action above its autonomous risk threshold."
              />
            ) : (
              <ul className="approval-list">
                {data.pending.map((request) => (
                  <li key={request.request_id} className="approval-card">
                    <header className="approval-head">
                      <StatusBadge value={request.risk} kind="risk" />
                      <span className="mono">{request.request_id}</span>
                      <span className="muted mono">{formatClock(request.created_at)}</span>
                    </header>
                    <p className="action-line mono">{request.action}</p>
                    {request.target ? (
                      <p className="muted mono" title={request.target}>
                        target {truncate(request.target, 70)}
                      </p>
                    ) : null}
                    {request.reason ? <p>{request.reason}</p> : null}
                    {request.expected_effect ? (
                      <p className="muted">
                        expected effect: <span className="mono">{joinList(request.expected_effect)}</span>
                      </p>
                    ) : null}
                    {request.evidence && request.evidence.length > 0 ? (
                      <ul className="chip-list">
                        {request.evidence.map((item, index) => (
                          <li key={`${item}-${index}`} className="chip mono">
                            {truncate(item, 40)}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    <div className="form-actions">
                      <button
                        type="button"
                        className="btn btn-small btn-primary"
                        disabled={decidingId === request.request_id}
                        onClick={() => decide(request.request_id, 'APPROVE')}
                      >
                        approve
                      </button>
                      <button
                        type="button"
                        className="btn btn-small btn-danger"
                        disabled={decidingId === request.request_id}
                        onClick={() => decide(request.request_id, 'REJECT')}
                      >
                        reject
                      </button>
                      <button
                        type="button"
                        className="btn btn-small"
                        disabled={decidingId === request.request_id}
                        onClick={() => decide(request.request_id, 'PAUSE')}
                      >
                        pause
                      </button>
                      <button
                        type="button"
                        className="btn btn-small"
                        disabled={decidingId === request.request_id}
                        onClick={() => decide(request.request_id, 'STOP')}
                      >
                        stop
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            <KeyValues
              entries={[
                { label: 'endpoint', value: `/api/targets/${targetId}/approvals`, mono: true },
                { label: 'decisions recorded', value: data.decisions ?? 0 },
              ]}
            />
          </>
        )}
      </ResourceView>
    </Panel>
  );
}
