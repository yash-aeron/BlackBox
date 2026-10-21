import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { EvidenceRecord, GraphEdge, GraphResponse, TransitionDetail } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import {
  count,
  effectText,
  formatDateTime,
  joinList,
  normalizeToken,
  truncate,
} from '../lib/util';
import { ErrorState, KeyValues, Loading } from './Panel';
import { ConfidenceBar } from './ConfidenceBar';
import { StatusBadge } from './StatusBadge';

export interface GraphEdgePanelProps {
  targetId: string;
  edge: GraphEdge;
  graph: GraphResponse;
  onClose: () => void;
  onSelectNode: (nodeId: string) => void;
}

function EffectList({ items, emptyMessage }: { items: string[]; emptyMessage: string }): ReactElement {
  if (items.length === 0) return <p className="muted">{emptyMessage}</p>;
  return (
    <ul className="effect-list">
      {items.map((item, index) => (
        <li key={`${item}-${index}`} className="mono">
          {item}
        </li>
      ))}
    </ul>
  );
}

/**
 * Side panel for one transition: the action, its predicted effect, what was
 * actually observed, and the evidence records behind the claim.
 */
export function GraphEdgePanel({
  targetId,
  edge,
  graph,
  onClose,
  onSelectNode,
}: GraphEdgePanelProps): ReactElement {
  // `edge.id` is the transition id when the graph was built by the agent, so the
  // detail lookup is best-effort: a graph from another producer still renders.
  const transition = useApiData<TransitionDetail>(
    () => api.transition(targetId, edge.id),
    [targetId, edge.id],
    { optional: true },
  );
  const evidence = useApiData<EvidenceRecord[]>(
    () => api.evidence(targetId, { transition_id: edge.id, limit: 50 }),
    [targetId, edge.id],
  );

  const detail = transition.data;
  const expected = detail?.action.expected_effect ?? edge.effects ?? [];
  const observed = (detail?.observed_effects ?? []).map(effectText);
  const execution = count(detail?.execution_count);
  const success = count(detail?.success_count ?? edge.success_count);
  const failure = count(detail?.failure_count ?? edge.failure_count);
  const successRate = execution > 0 ? `${Math.round((success / execution) * 100)}%` : '—';

  const nodeLabel = (stateId: string): string =>
    graph.nodes.find((node) => node.id === stateId)?.label || stateId;

  return (
    <div className="side-panel">
      <header className="side-head">
        <span className="side-kind">transition</span>
        <span className="mono side-id" title={edge.id}>
          {truncate(edge.id, 22)}
        </span>
        <button type="button" className="btn btn-small" onClick={onClose} aria-label="close transition panel">
          close
        </button>
      </header>

      <div className="side-body">
        <p className="badge-row">
          <StatusBadge value={edge.status} />
          <StatusBadge value={edge.risk} kind="risk" />
          {edge.verified ? <StatusBadge value="verified" kind="kind" /> : null}
          <ConfidenceBar value={edge.confidence ?? detail?.confidence} />
        </p>

        <p className="action-line mono">{edge.action}</p>
        <p className="muted mono">
          type {edge.action_type ?? detail?.action.type ?? '—'}
          {detail?.action.origin ? ` · origin ${detail.action.origin}` : ''}
        </p>

        <div className="endpoint-row">
          <button type="button" className="btn btn-small" onClick={() => onSelectNode(edge.source)}>
            ← {truncate(nodeLabel(edge.source), 20)}
          </button>
          <span className="muted mono">{truncate(edge.source, 12)}</span>
          <span className="arrow" aria-hidden="true">
            →
          </span>
          <button type="button" className="btn btn-small" onClick={() => onSelectNode(edge.target)}>
            {truncate(nodeLabel(edge.target), 20)} →
          </button>
          <span className="muted mono">{truncate(edge.target, 12)}</span>
        </div>

        <h3 className="side-sub">expected vs observed effects</h3>
        <div className="effect-columns">
          <div>
            <h4 className="effect-head">expected</h4>
            <EffectList items={expected} emptyMessage="no effect was predicted" />
          </div>
          <div>
            <h4 className="effect-head">observed</h4>
            <EffectList items={observed} emptyMessage="no effect was observed" />
          </div>
        </div>

        {!detail && !transition.loading ? (
          <p className="muted">
            transition detail unavailable for this edge id — showing graph-level data only.
          </p>
        ) : null}

        <KeyValues
          entries={[
            { label: 'executions', value: execution },
            { label: 'successes', value: success },
            { label: 'failures', value: failure },
            { label: 'success rate', value: successRate, mono: true },
            {
              label: 'preconditions',
              value: detail?.preconditions?.length ? joinList(detail.preconditions) : '—',
            },
            {
              label: 'postconditions',
              value: detail?.postconditions?.length ? joinList(detail.postconditions) : '—',
            },
            { label: 'updated', value: formatDateTime(detail?.updated_at) },
          ]}
        />

        <h3 className="side-sub">
          evidence ({detail?.evidence_ids?.length ?? edge.evidence_ids?.length ?? 0} referenced)
        </h3>
        {evidence.loading && evidence.data === null ? <Loading label="loading evidence…" /> : null}
        {evidence.error ? <ErrorState error={evidence.error} onRetry={evidence.reload} /> : null}
        {evidence.data && evidence.data.length === 0 ? (
          <p className="state state-empty">no evidence records returned for this transition</p>
        ) : null}
        {evidence.data && evidence.data.length > 0 ? (
          <ul className="evidence-list">
            {evidence.data.map((record) => (
              <li key={record.evidence_id} className="evidence-row">
                <div className="evidence-head">
                  <span className="mono">{truncate(record.evidence_id, 18)}</span>
                  <StatusBadge value={record.kind} kind="kind" />
                  <span className="muted mono">{formatDateTime(record.created_at)}</span>
                </div>
                <p>{record.message || '(no message)'}</p>
                {record.effects && record.effects.length > 0 ? (
                  <p className="mono muted">{record.effects.map(effectText).join(' · ')}</p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}

        {detail?.evidence_ids && detail.evidence_ids.length > 0 ? (
          <p className="muted mono">ids: {detail.evidence_ids.map((id) => truncate(id, 14)).join(', ')}</p>
        ) : null}

        <p className="muted mono">
          state of this edge: {normalizeToken(edge.status).toLowerCase()}
          {edge.success_count === 0 ? ' · never succeeded (dashed in the graph)' : ''}
        </p>
      </div>
    </div>
  );
}
