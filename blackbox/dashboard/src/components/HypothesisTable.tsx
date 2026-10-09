import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { Hypothesis } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { formatDateTime, truncate } from '../lib/util';
import { Panel, ResourceView } from './Panel';
import { ConfidenceBar } from './ConfidenceBar';
import { StatusBadge } from './StatusBadge';

export interface HypothesisTableProps {
  targetId: string;
}

/** Learned hypotheses, with the evidence and the prediction behind each one. */
export function HypothesisTable({ targetId }: HypothesisTableProps): ReactElement {
  const resource = useApiData<Hypothesis[]>(() => api.hypotheses(targetId), [targetId]);
  const rows = resource.data ?? [];

  return (
    <Panel
      title="Hypotheses"
      subtitle={`${rows.length} statement${rows.length === 1 ? '' : 's'}`}
      actions={
        <button type="button" className="btn btn-small" onClick={resource.reload}>
          refresh
        </button>
      }
    >
      <ResourceView
        resource={resource}
        isEmpty={(data) => data.length === 0}
        emptyMessage="no hypotheses learned yet — run an exploration"
        emptyHint="hypotheses are claims the agent is deliberately trying to falsify by experiment."
      >
        {(data) => (
          <table className="tbl tbl-hypotheses">
            <thead>
              <tr>
                <th scope="col">id</th>
                <th scope="col">statement</th>
                <th scope="col">kind</th>
                <th scope="col">status</th>
                <th scope="col">confidence</th>
                <th scope="col">support</th>
                <th scope="col">contradict</th>
                <th scope="col">updated</th>
              </tr>
            </thead>
            <tbody>
              {data.map((hypothesis) => (
                <tr key={hypothesis.hypothesis_id}>
                  <td className="mono" title={hypothesis.hypothesis_id}>
                    {truncate(hypothesis.hypothesis_id, 14)}
                  </td>
                  <td>
                    <span>{hypothesis.statement}</span>
                    {hypothesis.subject ? <span className="muted"> · {hypothesis.subject}</span> : null}
                    {hypothesis.prediction && Object.keys(hypothesis.prediction).length > 0 ? (
                      <details className="inline-details">
                        <summary>prediction</summary>
                        <pre className="json mono">{JSON.stringify(hypothesis.prediction, null, 2)}</pre>
                      </details>
                    ) : null}
                    {hypothesis.evidence && hypothesis.evidence.length > 0 ? (
                      <details className="inline-details">
                        <summary>{hypothesis.evidence.length} evidence</summary>
                        <ul className="effect-list">
                          {hypothesis.evidence.map((id, index) => (
                            <li key={`${id}-${index}`} className="mono">
                              {id}
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                  </td>
                  <td className="mono">{hypothesis.kind ?? '—'}</td>
                  <td>
                    <StatusBadge value={hypothesis.status} />
                  </td>
                  <td>
                    <ConfidenceBar value={hypothesis.confidence} label="" />
                  </td>
                  <td className="mono">{hypothesis.supporting_experiments?.length ?? 0}</td>
                  <td className="mono">{hypothesis.contradicting_experiments?.length ?? 0}</td>
                  <td className="mono">{formatDateTime(hypothesis.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ResourceView>
    </Panel>
  );
}
