import { useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { API_BASE, api } from './api/client';
import type { GraphResponse, HealthResponse } from './api/types';
import { AgentStatusPanel } from './components/AgentStatusPanel';
import { ApprovalQueue } from './components/ApprovalQueue';
import { ConstraintTable } from './components/ConstraintTable';
import { CurrentStatePanel } from './components/CurrentStatePanel';
import { EvidenceList } from './components/EvidenceList';
import { ExperimentTable } from './components/ExperimentTable';
import { GraphView } from './components/GraphView';
import { HypothesisTable } from './components/HypothesisTable';
import { LiveLog } from './components/LiveLog';
import { MetricsPanel } from './components/MetricsPanel';
import { EmptyState, ErrorState, Loading } from './components/Panel';
import { TargetPicker } from './components/TargetPicker';
import { TaskRunner } from './components/TaskRunner';
import { ViewTabs } from './components/ViewTabs';
import type { ViewTabDefinition } from './components/ViewTabs';
import { WorkflowList } from './components/WorkflowList';
import { useApiData } from './hooks/useApiData';
import { useEventStream } from './hooks/useEventStream';
import { useTarget } from './hooks/useTarget';
import { classNames, truncate } from './lib/util';

const TABS: ViewTabDefinition[] = [
  { id: 'graph', label: 'Graph', hint: 'the learned state graph' },
  { id: 'workflows', label: 'Workflows', hint: 'mined parameterized workflows' },
  { id: 'model', label: 'Model', hint: 'hypotheses and constraints' },
  { id: 'evidence', label: 'Evidence', hint: 'the audit trail behind every claim' },
  { id: 'experiments', label: 'Experiments', hint: 'runs, seeds and metrics' },
  { id: 'tasks', label: 'Tasks', hint: 'run a goal against the learned model' },
  { id: 'live', label: 'Live', hint: 'the live agent event stream' },
  { id: 'metrics', label: 'Metrics', hint: 'counts and capture metrics' },
  { id: 'status', label: 'Status', hint: 'browser, model, policy and model I/O' },
  { id: 'approvals', label: 'Approvals', hint: 'risky actions waiting for a human' },
];

export default function App(): ReactElement {
  const target = useTarget();
  const [view, setView] = useState<string>('graph');

  const targetId = target.selectedId;
  const graph = useApiData<GraphResponse>(
    targetId ? () => api.graph(targetId) : null,
    [targetId],
  );
  const stream = useEventStream(targetId);
  const health = useApiData<HealthResponse>(() => api.health(), []);

  const activeTab = TABS.find((tab) => tab.id === view) ?? TABS[0];
  const noTargets = !target.loading && !target.error && target.targets.length === 0;

  const renderView = (): ReactNode => {
    if (!targetId) return null;
    switch (view) {
      case 'graph':
        return (
          <GraphView
            targetId={targetId}
            graph={graph.data}
            loading={graph.loading}
            error={graph.error}
            onReload={graph.reload}
          />
        );
      case 'workflows':
        return <WorkflowList targetId={targetId} />;
      case 'model':
        return (
          <div className="stack">
            <HypothesisTable targetId={targetId} />
            <ConstraintTable targetId={targetId} />
          </div>
        );
      case 'evidence':
        return <EvidenceList targetId={targetId} limit={200} />;
      case 'experiments':
        return <ExperimentTable targetId={targetId} />;
      case 'tasks':
        return <TaskRunner targetId={targetId} />;
      case 'live':
        return (
          <LiveLog
            targetId={targetId}
            events={stream.events}
            connected={stream.connected}
            paused={stream.paused}
            onPausedChange={stream.setPaused}
            bufferedCount={stream.bufferedCount}
            onClear={stream.clear}
          />
        );
      case 'metrics':
        return <MetricsPanel targetId={targetId} />;
      case 'status':
        return <AgentStatusPanel targetId={targetId} />;
      case 'approvals':
        return <ApprovalQueue targetId={targetId} />;
      default:
        return <EmptyState message={`unknown view ${view}`} />;
    }
  };

  return (
    <div className="app">
      <header className="app-head">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            BB
          </span>
          <h1>BlackBox</h1>
          <span className="brand-sub">behavioural model dashboard</span>
        </div>

        <TargetPicker
          targets={target.targets}
          selectedId={target.selectedId}
          loading={target.loading}
          error={null}
          onSelect={target.selectTarget}
          onReload={target.reload}
          onRegistered={(registered) => {
            target.reload();
            target.selectTarget(registered.target_id);
          }}
        />

        <div className="head-meta">
          <span
            className={classNames('conn', stream.connected ? 'conn-on' : 'conn-off')}
            role="status"
            title={stream.error ?? undefined}
          >
            {stream.connected ? 'stream connected' : 'stream offline'}
          </span>
          {health.data ? (
            <span className="mono muted">
              api {health.data.status} · browser {truncate(health.data.browser, 40)} · v{health.data.version}
            </span>
          ) : null}
          {health.error ? (
            <span className="mono err-text" title={health.error.message}>
              api unreachable
            </span>
          ) : null}
        </div>
      </header>

      {target.error ? <ErrorState error={target.error} onRetry={target.reload} /> : null}
      {target.loading && target.targets.length === 0 && !target.error ? (
        <Loading label="loading targets…" />
      ) : null}
      {noTargets ? (
        <EmptyState
          message="no targets registered"
          hint="open “register target…” in the header: a target needs an id, a base URL and the origins you are authorized to touch."
        />
      ) : null}

      {targetId ? (
        <>
          <CurrentStatePanel targetId={targetId} graph={graph.data} events={stream.events} />

          <ViewTabs tabs={TABS} active={view} onChange={setView} />

          <main
            className="view"
            role="tabpanel"
            id={`panel-${activeTab.id}`}
            aria-labelledby={`tab-${activeTab.id}`}
            tabIndex={-1}
          >
            {renderView()}
          </main>

          <footer className="app-foot mono muted">
            api base {API_BASE || '(same origin — vite proxy to http://127.0.0.1:8099)'} · target {targetId} ·
            stream {stream.events.length} events
          </footer>
        </>
      ) : null}
    </div>
  );
}
