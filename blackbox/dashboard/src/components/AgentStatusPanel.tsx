import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { AgentStatus } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import { count, describeValue, toError } from '../lib/util';
import { ErrorState, KeyValues, Panel, ResourceView } from './Panel';
import { StatusBadge } from './StatusBadge';

export interface AgentStatusPanelProps {
  targetId: string;
}

const MODEL_COUNT_KEYS = [
  'states',
  'transitions',
  'verified_transitions',
  'workflows',
  'constraints',
  'hypotheses',
  'pages',
  'evidence',
  'experiments',
];

/** Browser, model, policy and settings for the target's live agent. */
export function AgentStatusPanel({ targetId }: AgentStatusPanelProps): ReactElement {
  const resource = useApiData<AgentStatus>(() => api.targetStatus(targetId), [targetId]);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [exportedPath, setExportedPath] = useState<string | null>(null);
  const [importPath, setImportPath] = useState('');
  const [ioBusy, setIoBusy] = useState(false);
  const [ioError, setIoError] = useState<unknown>(null);
  const [ioNotice, setIoNotice] = useState<string | null>(null);

  const reload = resource.reload;

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(reload, 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, reload]);

  const exportModel = (): void => {
    setIoBusy(true);
    setIoError(null);
    setIoNotice(null);
    api.exportModel(targetId).then(
      (result) => {
        setIoBusy(false);
        setExportedPath(result?.path ?? null);
        setIoNotice('model exported');
      },
      (cause: unknown) => {
        setIoBusy(false);
        setIoError(toError(cause));
      },
    );
  };

  const importModel = (): void => {
    const path = importPath.trim();
    if (!path) {
      setIoError(new Error('enter the path of a model file to import'));
      return;
    }
    setIoBusy(true);
    setIoError(null);
    setIoNotice(null);
    api.importModel(targetId, path).then(
      (result) => {
        setIoBusy(false);
        setIoNotice(`model imported from ${path}: ${describeValue(result) || 'ok'}`);
        reload();
      },
      (cause: unknown) => {
        setIoBusy(false);
        setIoError(toError(cause));
      },
    );
  };

  return (
    <Panel
      title="Agent status"
      subtitle="browser, model, policy and settings"
      actions={
        <>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            />
            <span>auto refresh (5s)</span>
          </label>
          <button type="button" className="btn btn-small" onClick={reload}>
            refresh
          </button>
        </>
      }
    >
      <ResourceView resource={resource} loadingLabel="loading agent status…" emptyMessage="no status returned">
        {(status: AgentStatus) => {
          const browser = status.browser ?? null;
          const sandbox = browser?.sandbox ?? status.sandbox ?? null;
          const policy = status.policy ?? null;
          const approvals = policy?.approvals ?? null;
          const model = status.model ?? null;
          const settings = status.settings ?? {};
          const blocked = sandbox?.blocked ?? [];

          return (
            <>
              <p className="badge-row">
                <StatusBadge value={browser?.alive ? 'BROWSER ALIVE' : 'BROWSER DOWN'} />
                <StatusBadge value={status.mode ?? 'UNKNOWN'} kind="mode" />
                {browser?.headless ? <StatusBadge value="headless" kind="kind" /> : null}
                <StatusBadge value={`${count(browser?.restarts)} restarts`} kind="kind" />
                <StatusBadge value={`${count(browser?.crashes)} crashes`} kind="kind" />
              </p>

              <h3 className="side-sub">browser</h3>
              <KeyValues
                entries={[
                  { label: 'version', value: browser?.browser_version ?? '—', mono: true },
                  { label: 'profile', value: browser?.profile ?? '—', mono: true },
                  {
                    label: 'last error',
                    value: browser?.last_error ? (
                      <span className="err-text">{browser.last_error}</span>
                    ) : (
                      'none'
                    ),
                  },
                  { label: 'allowed origins', value: count(sandbox?.allowed_origins?.length) },
                  { label: 'blocked actions', value: count(sandbox?.blocked_count) },
                ]}
              />
              {sandbox?.allowed_origins && sandbox.allowed_origins.length > 0 ? (
                <ul className="chip-list">
                  {sandbox.allowed_origins.map((origin) => (
                    <li key={origin} className="chip mono">
                      {origin}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">no origins allow-listed</p>
              )}
              {blocked.length > 0 ? (
                <details className="inline-details">
                  <summary>{blocked.length} blocked action(s)</summary>
                  <ul className="effect-list">
                    {blocked.map((item, index) => (
                      <li key={`${item}-${index}`} className="mono">
                        {item}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              <h3 className="side-sub">model</h3>
              <div className="metric-grid metric-grid-compact">
                {MODEL_COUNT_KEYS.map((key) => (
                  <div className="metric" key={key}>
                    <span className="metric-value mono">{count(model?.[key])}</span>
                    <span className="metric-label">{key.replace(/_/g, ' ')}</span>
                  </div>
                ))}
              </div>

              <h3 className="side-sub">policy</h3>
              <p className="badge-row">
                <StatusBadge
                  value={policy?.allow_low_actions === false ? 'low blocked' : 'low allowed'}
                  kind="kind"
                />
                <StatusBadge
                  value={policy?.allow_medium_actions ? 'medium allowed' : 'medium needs approval'}
                  kind="kind"
                />
                <StatusBadge
                  value={policy?.allow_high_actions ? 'high allowed' : 'high needs approval'}
                  kind="kind"
                />
                <StatusBadge
                  value={policy?.allow_critical_actions ? 'critical allowed' : 'critical blocked'}
                  kind="kind"
                />
              </p>
              <KeyValues
                entries={[
                  { label: 'pending approvals', value: count(approvals?.pending?.length) },
                  { label: 'decisions', value: count(approvals?.decisions) },
                  { label: 'approved', value: count(approvals?.approved) },
                  { label: 'rejected', value: count(approvals?.rejected) },
                ]}
              />

              <h3 className="side-sub">settings</h3>
              {Object.keys(settings).length === 0 ? (
                <p className="muted">no settings reported</p>
              ) : (
                <KeyValues
                  entries={Object.entries(settings).map(([key, value]) => ({
                    label: key.replace(/_/g, ' '),
                    value: describeValue(value) || '—',
                    mono: true,
                  }))}
                />
              )}

              <h3 className="side-sub">model I/O</h3>
              <div className="form-actions">
                <button type="button" className="btn btn-small" onClick={exportModel} disabled={ioBusy}>
                  export model
                </button>
                <label className="field-inline" htmlFor="model-import-path">
                  <span className="field-label">import path</span>
                  <input
                    id="model-import-path"
                    className="input mono"
                    value={importPath}
                    onChange={(event) => setImportPath(event.target.value)}
                    placeholder="/data/models/demo-shop.json"
                  />
                </label>
                <button type="button" className="btn btn-small" onClick={importModel} disabled={ioBusy}>
                  import model
                </button>
              </div>
              {exportedPath ? (
                <p className="muted mono">exported to {exportedPath}</p>
              ) : null}
              {ioNotice ? <p className="notice-text">{ioNotice}</p> : null}
              {ioError ? <ErrorState error={ioError} /> : null}
            </>
          );
        }}
      </ResourceView>
    </Panel>
  );
}
