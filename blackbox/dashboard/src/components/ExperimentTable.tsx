import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { Experiment } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { describeValue, formatDateTime, formatDuration, truncate } from '../lib/util';
import { Panel, ResourceView } from './Panel';
import { StatusBadge } from './StatusBadge';

export interface ExperimentTableProps {
  targetId: string;
}

function experimentDuration(experiment: Experiment): number | null {
  const start = typeof experiment.started_at === 'number' ? experiment.started_at * 1000 : Date.parse(String(experiment.started_at ?? ''));
  const finish =
    typeof experiment.finished_at === 'number'
      ? experiment.finished_at * 1000
      : Date.parse(String(experiment.finished_at ?? ''));
  if (!Number.isFinite(start) || !Number.isFinite(finish) || finish < start) return null;
  return (finish - start) / 1000;
}

/** Exploration and task experiments with the metrics each one recorded. */
export function ExperimentTable({ targetId }: ExperimentTableProps): ReactElement {
  const resource = useApiData<Experiment[]>(() => api.experiments(targetId), [targetId]);
  const rows = resource.data ?? [];

  return (
    <Panel
      title="Experiments"
      subtitle={`${rows.length} run${rows.length === 1 ? '' : 's'}`}
      actions={
        <button type="button" className="btn btn-small" onClick={resource.reload}>
          refresh
        </button>
      }
    >
      <ResourceView
        resource={resource}
        isEmpty={(data) => data.length === 0}
        emptyMessage="no experiments recorded yet — run an exploration"
        emptyHint="every run is logged with its seed, browser/model version and metrics so results can be reproduced."
      >
        {(data) => (
          <table className="tbl tbl-experiments">
            <thead>
              <tr>
                <th scope="col">id</th>
                <th scope="col">kind</th>
                <th scope="col">status</th>
                <th scope="col">seed</th>
                <th scope="col">browser</th>
                <th scope="col">model</th>
                <th scope="col">started</th>
                <th scope="col">duration</th>
                <th scope="col">metrics</th>
              </tr>
            </thead>
            <tbody>
              {data.map((experiment) => {
                const metrics = Object.entries(experiment.metrics ?? {});
                return (
                  <tr key={experiment.experiment_id}>
                    <td className="mono" title={experiment.experiment_id}>
                      {truncate(experiment.experiment_id, 16)}
                    </td>
                    <td className="mono">{experiment.kind}</td>
                    <td>
                      <StatusBadge value={experiment.status} />
                    </td>
                    <td className="mono">{experiment.seed ?? '—'}</td>
                    <td className="mono">{truncate(experiment.browser_version ?? '—', 18)}</td>
                    <td className="mono">
                      v{experiment.model_version ?? '?'}
                      {experiment.prompt_version ? ` · ${experiment.prompt_version}` : ''}
                    </td>
                    <td className="mono">{formatDateTime(experiment.started_at)}</td>
                    <td className="mono">{formatDuration(experimentDuration(experiment))}</td>
                    <td className="mono">
                      {metrics.length === 0 ? (
                        <span className="muted">—</span>
                      ) : (
                        <details className="inline-details">
                          <summary>{metrics.length} metric{metrics.length === 1 ? '' : 's'}</summary>
                          <ul className="effect-list">
                            {metrics.map(([key, value]) => (
                              <li key={key} className="mono">
                                {key}={describeValue(value)}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </ResourceView>
    </Panel>
  );
}
