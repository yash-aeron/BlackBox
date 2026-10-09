import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { MetricsResponse } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { count, describeValue, formatMs, readNumber } from '../lib/util';
import { KeyValues, Panel, ResourceView } from './Panel';

export interface MetricsPanelProps {
  targetId: string;
}

/** Model counts, capture cost and guard activity for one target. */
export function MetricsPanel({ targetId }: MetricsPanelProps): ReactElement {
  const resource = useApiData<MetricsResponse>(() => api.metrics(targetId), [targetId]);

  return (
    <Panel
      title="Metrics"
      subtitle="counts, observation cost and network guard activity"
      actions={
        <button type="button" className="btn btn-small" onClick={resource.reload}>
          refresh
        </button>
      }
    >
      <ResourceView
        resource={resource}
        loadingLabel="loading metrics…"
        emptyMessage="the API returned no metrics for this target"
      >
        {(metrics: MetricsResponse) => {
          const observation = metrics.observation ?? null;
          const sandbox = metrics.sandbox ?? null;
          const guard = metrics.network_guard ?? null;
          const meanCapture = readNumber(observation?.mean_capture_ms);
          const tiles: Array<{ label: string; value: string | number }> = [
            { label: 'states', value: count(metrics.states) },
            { label: 'transitions', value: count(metrics.transitions) },
            { label: 'verified transitions', value: count(metrics.verified_transitions) },
            { label: 'workflows', value: count(metrics.workflows) },
            { label: 'constraints', value: count(metrics.constraints) },
            { label: 'hypotheses', value: count(metrics.hypotheses) },
            { label: 'evidence', value: count(metrics.evidence) },
            { label: 'experiments', value: count(metrics.experiments) },
            { label: 'pages', value: count(metrics.pages) },
            { label: 'model version', value: metrics.model_version ?? '—' },
          ];
          return (
            <>
              <div className="metric-grid">
                {tiles.map((tile) => (
                  <div className="metric" key={tile.label}>
                    <span className="metric-value mono">{tile.value}</span>
                    <span className="metric-label">{tile.label}</span>
                  </div>
                ))}
              </div>

              <h3 className="side-sub">observation</h3>
              <KeyValues
                entries={[
                  { label: 'observations', value: count(observation?.observations) },
                  {
                    label: 'mean capture',
                    value: meanCapture === null ? '—' : formatMs(meanCapture),
                    mono: true,
                  },
                ]}
              />

              <h3 className="side-sub">sandbox</h3>
              <KeyValues
                entries={[{ label: 'blocked actions', value: count(sandbox?.blocked_count) }]}
              />

              <h3 className="side-sub">network guard</h3>
              <KeyValues
                entries={[
                  { label: 'observed requests', value: count(guard?.observed_requests) },
                  { label: 'blocked agent requests', value: count(guard?.blocked_agent_requests) },
                  {
                    label: 'policy',
                    value:
                      guard?.policy === null || guard?.policy === undefined
                        ? '—'
                        : typeof guard.policy === 'string'
                          ? guard.policy
                          : describeValue(guard.policy),
                    mono: true,
                  },
                ]}
              />
            </>
          );
        }}
      </ResourceView>
    </Panel>
  );
}
