import { useMemo, useState } from 'react';
import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { EvidenceRecord } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { effectText, formatDateTime, normalizeToken, truncate } from '../lib/util';
import { EmptyState, KeyValues, Panel, ResourceView } from './Panel';
import { ScreenshotView } from './ScreenshotView';
import { StatusBadge } from './StatusBadge';

export interface EvidenceListProps {
  targetId: string;
  /** Restrict to one transition, e.g. the edge selected in the graph. */
  transitionId?: string | null;
  limit?: number;
}

/** The audit trail: every claim in the model traces back to one of these rows. */
export function EvidenceList({
  targetId,
  transitionId = null,
  limit = 200,
}: EvidenceListProps): ReactElement {
  const [kindFilter, setKindFilter] = useState('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const resource = useApiData<EvidenceRecord[]>(
    () => api.evidence(targetId, { transition_id: transitionId, limit }),
    [targetId, transitionId, limit],
  );
  const detail = useApiData<EvidenceRecord>(
    selectedId ? () => api.evidenceRecord(targetId, selectedId) : null,
    [targetId, selectedId],
    { optional: true },
  );

  const rows = resource.data ?? [];
  const kinds = useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows) seen.add(normalizeToken(row.kind));
    return ['ALL', ...[...seen].sort()];
  }, [rows]);
  const filtered = kindFilter === 'ALL' ? rows : rows.filter((row) => normalizeToken(row.kind) === kindFilter);
  const selected = detail.data ?? filtered.find((row) => row.evidence_id === selectedId) ?? null;

  return (
    <div className="split">
      <Panel
        title="Evidence"
        subtitle={`${filtered.length}${kindFilter === 'ALL' ? '' : ` of ${rows.length}`} record${
          filtered.length === 1 ? '' : 's'
        }${transitionId ? ` · transition ${truncate(transitionId, 14)}` : ''}`}
        actions={
          <>
            <label className="field-inline" htmlFor="evidence-kind">
              <span className="field-label">kind</span>
              <select
                id="evidence-kind"
                className="input mono"
                value={kindFilter}
                onChange={(event) => setKindFilter(event.target.value)}
              >
                {kinds.map((kind) => (
                  <option key={kind} value={kind}>
                    {kind.toLowerCase()}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="btn btn-small" onClick={resource.reload}>
              refresh
            </button>
          </>
        }
      >
        <ResourceView
          resource={resource}
          isEmpty={(data) => data.length === 0}
          emptyMessage="no evidence recorded yet — run an exploration"
          emptyHint="evidence is written for every observation, action result, block and human decision."
        >
          {() =>
            filtered.length === 0 ? (
              <EmptyState message={`no ${kindFilter.toLowerCase()} evidence in this result set`} />
            ) : (
              <ul className="rows rows-tight">
                {filtered.map((record) => (
                  <li key={record.evidence_id}>
                    <button
                      type="button"
                      className={`row-btn${record.evidence_id === selectedId ? ' row-active' : ''}`}
                      aria-pressed={record.evidence_id === selectedId}
                      onClick={() => setSelectedId(record.evidence_id)}
                    >
                      <span className="row-meta mono">
                        {truncate(record.evidence_id, 16)} · {formatDateTime(record.created_at)}
                      </span>
                      <span className="row-title">
                        <StatusBadge value={record.kind} kind="kind" /> {record.message || '(no message)'}
                      </span>
                      <span className="row-meta mono">
                        {record.source_state ? `${truncate(record.source_state, 10)} → ` : ''}
                        {record.target_state ? truncate(record.target_state, 10) : 'no state change'}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )
          }
        </ResourceView>
      </Panel>

      <Panel title="Evidence detail" subtitle={selected ? selected.evidence_id : 'nothing selected'}>
        {!selectedId ? (
          <EmptyState message="select an evidence record" />
        ) : !selected ? (
          <EmptyState message="loading evidence detail…" />
        ) : (
          <>
            <p className="badge-row">
              <StatusBadge value={selected.kind} kind="kind" />
              <span className="mono muted">{formatDateTime(selected.created_at)}</span>
            </p>
            <p className="summary">{selected.message || '(no message)'}</p>
            <KeyValues
              entries={[
                { label: 'evidence id', value: selected.evidence_id, mono: true },
                { label: 'experiment', value: selected.experiment_id ?? '—', mono: true },
                { label: 'session', value: selected.session_id ?? '—', mono: true },
                { label: 'action', value: selected.action_id ?? '—', mono: true },
                { label: 'source state', value: selected.source_state ?? '—', mono: true },
                { label: 'target state', value: selected.target_state ?? '—', mono: true },
                { label: 'transition', value: selected.transition_id ?? '—', mono: true },
                { label: 'step', value: selected.step_index ?? '—' },
                { label: 'observations', value: selected.observations?.length ?? 0 },
              ]}
            />

            <h3 className="side-sub">effects ({selected.effects?.length ?? 0})</h3>
            {selected.effects && selected.effects.length > 0 ? (
              <ul className="effect-list">
                {selected.effects.map((effect, index) => (
                  <li key={index} className="mono">
                    {effectText(effect)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">no effects recorded</p>
            )}

            {selected.notes && selected.notes.length > 0 ? (
              <>
                <h3 className="side-sub">notes</h3>
                <ul className="effect-list">
                  {selected.notes.map((note, index) => (
                    <li key={`${note}-${index}`}>{note}</li>
                  ))}
                </ul>
              </>
            ) : null}

            {selected.screenshot_refs && selected.screenshot_refs.length > 0 ? (
              <>
                <h3 className="side-sub">screenshots ({selected.screenshot_refs.length})</h3>
                <div className="shot-grid">
                  {selected.screenshot_refs.map((ref) => (
                    <ScreenshotView key={ref} targetId={targetId} screenshotRef={ref} />
                  ))}
                </div>
              </>
            ) : null}
          </>
        )}
      </Panel>
    </div>
  );
}
