import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { Constraint } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { formatDateTime, joinList, truncate } from '../lib/util';
import { Panel, ResourceView } from './Panel';
import { ConfidenceBar } from './ConfidenceBar';
import { StatusBadge } from './StatusBadge';

export interface ConstraintTableProps {
  targetId: string;
}

/** Learned constraints: preconditions, blockers and site rules with their evidence. */
export function ConstraintTable({ targetId }: ConstraintTableProps): ReactElement {
  const resource = useApiData<Constraint[]>(() => api.constraints(targetId), [targetId]);
  const rows = resource.data ?? [];

  return (
    <Panel
      title="Constraints"
      subtitle={`${rows.length} rule${rows.length === 1 ? '' : 's'}`}
      actions={
        <button type="button" className="btn btn-small" onClick={resource.reload}>
          refresh
        </button>
      }
    >
      <ResourceView
        resource={resource}
        isEmpty={(data) => data.length === 0}
        emptyMessage="no constraints learned yet"
        emptyHint="constraints record what must be true before an action works — blocks, validations and required values."
      >
        {(data) => (
          <table className="tbl tbl-constraints">
            <thead>
              <tr>
                <th scope="col">id</th>
                <th scope="col">kind</th>
                <th scope="col">scope</th>
                <th scope="col">subject</th>
                <th scope="col">expression</th>
                <th scope="col">message</th>
                <th scope="col">status</th>
                <th scope="col">confidence</th>
                <th scope="col">evidence</th>
                <th scope="col">updated</th>
              </tr>
            </thead>
            <tbody>
              {data.map((constraint) => (
                <tr key={constraint.constraint_id}>
                  <td className="mono" title={constraint.constraint_id}>
                    {truncate(constraint.constraint_id, 14)}
                  </td>
                  <td className="mono">{constraint.kind}</td>
                  <td className="mono">{constraint.scope}</td>
                  <td className="mono">{truncate(constraint.subject || '—', 24)}</td>
                  <td className="mono" title={constraint.expression}>
                    {truncate(constraint.expression || '—', 48)}
                  </td>
                  <td>{constraint.message ?? '—'}</td>
                  <td>
                    <StatusBadge value={constraint.status} />
                  </td>
                  <td>
                    <ConfidenceBar value={constraint.confidence} label="" />
                  </td>
                  <td>
                    <span className="mono" title={joinList(constraint.supporting_evidence)}>
                      +{constraint.supporting_evidence?.length ?? 0} / −
                      {constraint.contradicting_evidence?.length ?? 0}
                    </span>
                    {(constraint.supporting_evidence?.length ?? 0) > 0 ? (
                      <details className="inline-details">
                        <summary>ids</summary>
                        <ul className="effect-list">
                          {constraint.supporting_evidence.map((id, index) => (
                            <li key={`${id}-${index}`} className="mono">
                              {id}
                            </li>
                          ))}
                          {constraint.contradicting_evidence.map((id, index) => (
                            <li key={`against-${id}-${index}`} className="mono err-text">
                              {id}
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                  </td>
                  <td className="mono">{formatDateTime(constraint.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ResourceView>
    </Panel>
  );
}
